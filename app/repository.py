from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    delete,
    Integer,
    String,
    create_engine,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.security import hash_password, verify_password


@dataclass(frozen=True)
class SiteUser:
    id: int
    username: str
    display_name: str
    expire_at: datetime | None
    telegram_id: int | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass(frozen=True)
class ReferralRow:
    username: str
    status: str
    bonus_days: int

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass(frozen=True)
class TelegramLinkResult:
    user: SiteUser
    bonus_days_added: int
    merged_existing_telegram_user: bool
    already_linked: bool
    remnawave_username_to_disable: str | None = None


@dataclass(frozen=True)
class AutopayInfo:
    enabled: bool
    amount: int | None = None
    currency: str | None = None
    subscription_period: str | None = None
    captured_at: datetime | None = None


@dataclass(frozen=True)
class PendingRegistrationCode:
    token: str
    email: str
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class PendingRegistrationData:
    token: str
    username: str
    email: str
    password_hash: str
    expires_at: datetime


_engine = create_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None
_SessionLocal = sessionmaker(bind=_engine) if _engine is not None else None
_demo_users: dict[str, SiteUser] = {}
_demo_password_hashes: dict[str, str] = {}
_demo_telegram_link_bonuses: set[int] = set()
_demo_pending_registrations: dict[str, PendingRegistrationData] = {}
_demo_pending_registration_codes: dict[str, str] = {}
_SiteBase = declarative_base()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SiteUserCredential(_SiteBase):
    __tablename__ = "site_user_credentials"

    user_id = Column(BigInteger, primary_key=True)
    password_hash = Column(String(256), nullable=False)


class SiteIdentity(_SiteBase):
    __tablename__ = "site_identities"

    login = Column(String(256), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)


class TelegramLinkBonus(_SiteBase):
    __tablename__ = "site_telegram_link_bonuses"

    user_id = Column(BigInteger, primary_key=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    days_added = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)


class SiteTrialGrant(_SiteBase):
    __tablename__ = "site_trial_grants"

    user_id = Column(BigInteger, primary_key=True)
    days_added = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)


class PendingSiteRegistration(_SiteBase):
    __tablename__ = "site_pending_registrations"

    token = Column(String(64), primary_key=True)
    username = Column(String(256), nullable=False, index=True)
    email = Column(String(256), nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)


def database_enabled() -> bool:
    return _SessionLocal is not None


def initialize_site_storage() -> None:
    if _engine is not None:
        _SiteBase.metadata.create_all(_engine)


def _common_models():
    try:
        from common.models.db import ReferralBonus, User, YkPayment, YkRecurrentPayment
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule is required when database access is enabled."
        ) from exc
    return ReferralBonus, User, YkPayment, YkRecurrentPayment


def _common_user_related_models():
    try:
        from common.models.db import (
            EventLog,
            ExpiredUsersNotification,
            ExtendSubscriptionNotification,
            NcUsersNotification,
            UserTrafficProgress,
        )
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule is required when database access is enabled."
        ) from exc
    return (
        EventLog,
        ExpiredUsersNotification,
        ExtendSubscriptionNotification,
        NcUsersNotification,
        UserTrafficProgress,
    )


def _synthetic_telegram_id(username: str) -> int:
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return -int(digest[:15], 16)


def generate_site_username() -> str:
    return f"site_{secrets.token_hex(8)}"


def normalize_login(value: str) -> str:
    return value.strip().lower()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def generate_registration_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _registration_code_hash(token: str, code: str) -> str:
    return hashlib.sha256(f"{token}:{code}".encode("utf-8")).hexdigest()


def _sqlite_next_user_id(session, User) -> int | None:
    if session.get_bind().dialect.name != "sqlite":
        return None
    return int(session.execute(select(func.coalesce(func.max(User.id), 0) + 1)).scalar_one())


def _find_login_owner(session, User, login: str):
    identity = session.get(SiteIdentity, login)
    if identity is not None:
        user = session.get(User, identity.user_id)
        if user is not None:
            return user
    return session.execute(select(User).where(User.username == login)).scalar_one_or_none()


def _add_site_identity(
    session,
    login: str | None,
    user_id: int,
    password_hash: str,
) -> None:
    if not login or not password_hash:
        return

    normalized_login = normalize_login(login)
    if not normalized_login:
        return

    identity = session.get(SiteIdentity, normalized_login)
    if identity is None:
        session.add(
            SiteIdentity(
                login=normalized_login,
                user_id=user_id,
                password_hash=password_hash,
            )
        )
    else:
        identity.user_id = user_id
        identity.password_hash = password_hash


def _demo_user(username: str) -> SiteUser | None:
    if username in _demo_users:
        return _demo_users[username]
    if username not in {"demo", "demo@shredder.local"}:
        return None
    user = SiteUser(
        id=1,
        username="demo",
        display_name="Демо клиент",
        expire_at=datetime.now(timezone.utc) + timedelta(days=12, hours=6),
        telegram_id=-1,
    )
    _demo_users[user.username] = user
    return user


def _site_user_from_db_user(user) -> SiteUser:
    display_name = user.username or str(user.id)
    return SiteUser(
        id=user.id,
        username=display_name,
        display_name=display_name,
        expire_at=user.expire_at,
        telegram_id=user.telegram_id,
    )


def get_user_by_id(user_id: int) -> SiteUser | None:
    if not database_enabled():
        for user in _demo_users.values():
            if user.id == user_id:
                return user
        return None

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        user = session.get(User, user_id)
        return _site_user_from_db_user(user) if user is not None else None


def get_user_by_username(username: str) -> SiteUser | None:
    username = normalize_login(username)
    if not database_enabled():
        return _demo_user(username)

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        user = _find_login_owner(session, User, username)
        if user is None:
            return None
        return _site_user_from_db_user(user)


def get_user_by_telegram_id(telegram_id: int) -> SiteUser | None:
    if telegram_id <= 0:
        return None

    if not database_enabled():
        for user in _demo_users.values():
            if user.telegram_id == telegram_id:
                return user
        return None

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        user = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        return _site_user_from_db_user(user) if user is not None else None


def authenticate_site_user(login: str, password: str) -> SiteUser | None:
    login = login.strip().lower()
    if not database_enabled():
        user = _demo_user(login)
        if user is None:
            return None
        if verify_site_password(user, password):
            return user
        return None

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        identity = session.get(SiteIdentity, login)
        if identity is not None:
            if not verify_password(password, identity.password_hash):
                return None
            user = session.get(User, identity.user_id)
            return _site_user_from_db_user(user) if user is not None else None

        user = session.execute(select(User).where(User.username == login)).scalar_one_or_none()
        if user is None:
            return None
        if login.startswith("site_"):
            return None
        credential = session.get(SiteUserCredential, user.id)
        if credential is None or not verify_password(password, credential.password_hash):
            return None
        return _site_user_from_db_user(user)


def create_user(username: str | None, expire_at: datetime | None, password: str) -> SiteUser:
    username = normalize_login(username or generate_site_username())
    return create_user_with_password_hash(
        username=username,
        email=None,
        expire_at=expire_at,
        password_hash=hash_password(password),
    )


def create_user_with_password_hash(
    username: str,
    email: str | None,
    expire_at: datetime | None,
    password_hash: str,
) -> SiteUser:
    username = normalize_login(username or generate_site_username())
    email = normalize_email(email) if email else None

    if not database_enabled():
        user = SiteUser(
            id=len(_demo_users) + 1,
            username=username,
            display_name=username,
            expire_at=expire_at,
            telegram_id=_synthetic_telegram_id(username),
        )
        _demo_users[username] = user
        _demo_password_hashes[username] = password_hash
        if email:
            _demo_users[email] = user
            _demo_password_hashes[email] = password_hash
        return user

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        if _find_login_owner(session, User, username) is not None:
            raise ValueError("username already exists")
        if email and _find_login_owner(session, User, email) is not None:
            raise ValueError("email already exists")

        db_user = User(
            username=username,
            telegram_id=_synthetic_telegram_id(username),
            expire_at=expire_at.replace(tzinfo=None) if expire_at and expire_at.tzinfo else expire_at,
        )
        next_user_id = _sqlite_next_user_id(session, User)
        if next_user_id is not None:
            db_user.id = next_user_id
        session.add(db_user)
        try:
            session.flush()
            session.add(
                SiteUserCredential(
                    user_id=db_user.id,
                    password_hash=password_hash,
                )
            )
            if email is None:
                _add_site_identity(session, username, db_user.id, password_hash)
            _add_site_identity(session, email, db_user.id, password_hash)
            session.add(
                SiteTrialGrant(
                    user_id=db_user.id,
                    days_added=settings.trial_period_days,
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("user already exists") from exc
        session.refresh(db_user)
        return SiteUser(
            id=db_user.id,
            username=db_user.username or str(db_user.id),
            display_name=db_user.username or str(db_user.id),
            expire_at=db_user.expire_at,
            telegram_id=db_user.telegram_id,
        )


def create_telegram_user(
    telegram_id: int,
    expire_at: datetime | None,
    telegram_username: str | None = None,
) -> SiteUser:
    if telegram_id <= 0:
        raise ValueError("telegram_id must be positive")

    username = str(telegram_id)

    if not database_enabled():
        user = SiteUser(
            id=len(_demo_users) + 1,
            username=username,
            display_name=username,
            expire_at=expire_at,
            telegram_id=telegram_id,
        )
        _demo_users[username] = user
        return user

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        existing_user = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if existing_user is not None:
            return _site_user_from_db_user(existing_user)

        username_owner = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if username_owner is not None:
            raise ValueError("username already exists")

        values = {
            "username": username,
            "telegram_id": telegram_id,
            "expire_at": (
                expire_at.replace(tzinfo=None)
                if expire_at and expire_at.tzinfo
                else expire_at
            ),
        }
        if hasattr(User, "telegram_username"):
            values["telegram_username"] = telegram_username
        next_user_id = _sqlite_next_user_id(session, User)
        if next_user_id is not None:
            values["id"] = next_user_id

        db_user = User(**values)
        session.add(db_user)
        try:
            session.flush()
            session.add(
                SiteTrialGrant(
                    user_id=db_user.id,
                    days_added=settings.trial_period_days,
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("telegram user already exists") from exc
        session.refresh(db_user)
        return _site_user_from_db_user(db_user)


def create_pending_registration(
    username: str | None,
    email: str,
    password: str,
) -> PendingRegistrationCode:
    username = normalize_login(username or generate_site_username())
    email = normalize_email(email)
    if not email or "@" not in email:
        raise ValueError("invalid email")

    token = secrets.token_urlsafe(32)
    code = generate_registration_code()
    expires_at = _utcnow_naive() + timedelta(
        seconds=settings.registration_code_ttl_seconds
    )
    password_hash = hash_password(password)

    if not database_enabled():
        if get_user_by_username(username) is not None:
            raise ValueError("username already exists")
        if get_user_by_username(email) is not None:
            raise ValueError("email already exists")
        _demo_pending_registrations[token] = PendingRegistrationData(
            token=token,
            username=username,
            email=email,
            password_hash=password_hash,
            expires_at=expires_at,
        )
        _demo_pending_registration_codes[token] = _registration_code_hash(token, code)
        return PendingRegistrationCode(token, email, code, expires_at)

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        if _find_login_owner(session, User, username) is not None:
            raise ValueError("username already exists")
        if _find_login_owner(session, User, email) is not None:
            raise ValueError("email already exists")

        session.execute(
            delete(PendingSiteRegistration).where(
                PendingSiteRegistration.expires_at <= _utcnow_naive()
            )
        )
        session.execute(
            delete(PendingSiteRegistration).where(
                (PendingSiteRegistration.username == username)
                | (PendingSiteRegistration.email == email)
            )
        )
        session.add(
            PendingSiteRegistration(
                token=token,
                username=username,
                email=email,
                password_hash=password_hash,
                code_hash=_registration_code_hash(token, code),
                expires_at=expires_at,
            )
        )
        session.commit()
        return PendingRegistrationCode(token, email, code, expires_at)


def get_pending_registration(token: str) -> PendingRegistrationData | None:
    if not token:
        return None

    now = _utcnow_naive()
    if not database_enabled():
        pending = _demo_pending_registrations.get(token)
        if pending is None or pending.expires_at <= now:
            _demo_pending_registrations.pop(token, None)
            _demo_pending_registration_codes.pop(token, None)
            return None
        return pending

    with _SessionLocal() as session:
        pending = session.get(PendingSiteRegistration, token)
        if pending is None:
            return None
        if pending.expires_at <= now:
            session.delete(pending)
            session.commit()
            return None
        return PendingRegistrationData(
            token=pending.token,
            username=pending.username,
            email=pending.email,
            password_hash=pending.password_hash,
            expires_at=pending.expires_at,
        )


def verify_pending_registration_code(token: str, code: str) -> PendingRegistrationData:
    if not token:
        raise ValueError("registration session expired")

    normalized_code = code.strip()
    if not normalized_code:
        raise ValueError("invalid registration code")

    now = _utcnow_naive()
    expected_hash = _registration_code_hash(token, normalized_code)
    if not database_enabled():
        pending = _demo_pending_registrations.get(token)
        code_hash = _demo_pending_registration_codes.get(token)
        if pending is None or code_hash is None or pending.expires_at <= now:
            _demo_pending_registrations.pop(token, None)
            _demo_pending_registration_codes.pop(token, None)
            raise ValueError("registration code expired")
        if not hmac.compare_digest(expected_hash, code_hash):
            raise ValueError("invalid registration code")
        return pending

    with _SessionLocal() as session:
        pending = session.get(PendingSiteRegistration, token)
        if pending is None or pending.expires_at <= now:
            if pending is not None:
                session.delete(pending)
                session.commit()
            raise ValueError("registration code expired")
        if pending.attempts >= 5:
            session.delete(pending)
            session.commit()
            raise ValueError("too many registration code attempts")
        if not hmac.compare_digest(expected_hash, pending.code_hash):
            pending.attempts += 1
            session.commit()
            raise ValueError("invalid registration code")
        return PendingRegistrationData(
            token=pending.token,
            username=pending.username,
            email=pending.email,
            password_hash=pending.password_hash,
            expires_at=pending.expires_at,
        )


def consume_pending_registration(
    pending: PendingRegistrationData,
    expire_at: datetime | None,
) -> SiteUser:
    user = create_user_with_password_hash(
        username=pending.username,
        email=pending.email,
        expire_at=expire_at,
        password_hash=pending.password_hash,
    )

    if not database_enabled():
        _demo_pending_registrations.pop(pending.token, None)
        _demo_pending_registration_codes.pop(pending.token, None)
        return user

    with _SessionLocal() as session:
        pending_row = session.get(PendingSiteRegistration, pending.token)
        if pending_row is not None:
            session.delete(pending_row)
            session.commit()
    return user


def user_has_site_password(user: SiteUser) -> bool:
    if not database_enabled():
        return user.username in _demo_password_hashes

    with _SessionLocal() as session:
        credential = session.get(SiteUserCredential, user.id)
        return credential is not None


def verify_site_password(user: SiteUser, password: str) -> bool:
    if not database_enabled():
        stored_hash = _demo_password_hashes.get(user.username)
        return stored_hash is not None and verify_password(password, stored_hash)

    with _SessionLocal() as session:
        credential = session.get(SiteUserCredential, user.id)
        if credential is None:
            return False
        return verify_password(password, credential.password_hash)


def get_autopay_info(user: SiteUser) -> AutopayInfo:
    if not database_enabled():
        return AutopayInfo(enabled=False)

    _, _, _, YkRecurrentPayment = _common_models()
    with _SessionLocal() as session:
        recurrent = session.execute(
            select(YkRecurrentPayment).where(YkRecurrentPayment.user_id == user.id)
        ).scalar_one_or_none()
        if recurrent is None:
            return AutopayInfo(enabled=False)
        return AutopayInfo(
            enabled=True,
            amount=recurrent.amount,
            currency=recurrent.currency,
            subscription_period=recurrent.subscription_period,
            captured_at=recurrent.captured_at,
        )


def cancel_autopay(user: SiteUser) -> bool:
    if not database_enabled():
        return False

    _, User, _, YkRecurrentPayment = _common_models()
    with _SessionLocal() as session:
        deleted = session.execute(
            delete(YkRecurrentPayment).where(YkRecurrentPayment.user_id == user.id)
        ).rowcount
        session.execute(
            update(User).where(User.id == user.id).values(autopay_allow=False)
        )
        session.commit()
        return bool(deleted)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _remaining_time(expire_at: datetime | None, now: datetime) -> timedelta:
    expire_at = _naive_utc(expire_at)
    if expire_at is None or expire_at <= now:
        return timedelta()
    return expire_at - now


def _has_trial_payment(session, model, user_id: int) -> bool:
    return (
        session.execute(
            select(model.id)
            .where(model.user_id == user_id, model.is_trial_promotion.is_(True))
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _has_any_payment(session, model, user_id: int) -> bool:
    return (
        session.execute(
            select(model.id)
            .where(model.user_id == user_id)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _trial_time_left(
    created_at: datetime | None,
    days_added: int | None,
    now: datetime,
) -> timedelta:
    if not created_at or not days_added or days_added <= 0:
        return timedelta()

    trial_ends_at = _naive_utc(created_at) + timedelta(days=days_added)
    if trial_ends_at <= now:
        return timedelta()
    return trial_ends_at - now


def _legacy_site_trial_time_left(session, user, now: datetime) -> timedelta:
    identity = (
        session.execute(
            select(SiteIdentity)
            .where(SiteIdentity.user_id == user.id)
            .order_by(SiteIdentity.created_at.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if identity is None:
        remaining = _remaining_time(user.expire_at, now)
        if remaining <= timedelta(days=settings.trial_period_days):
            return remaining
        return timedelta()
    return _trial_time_left(identity.created_at, settings.trial_period_days, now)


def _site_trial_time_to_skip(
    session,
    user,
    credential: SiteUserCredential | None,
    now: datetime,
) -> timedelta:
    grant = session.get(SiteTrialGrant, user.id)
    if grant is not None:
        return _trial_time_left(grant.created_at, grant.days_added, now)
    if credential is not None and user.telegram_id is not None and user.telegram_id < 0:
        return _legacy_site_trial_time_left(session, user, now)
    return timedelta()


def _free_period_already_used(
    session,
    user,
    credential: SiteUserCredential | None,
    YkPayment,
    YkRecurrentPayment,
    now: datetime,
) -> bool:
    if session.get(SiteTrialGrant, user.id) is not None:
        return True
    if session.get(TelegramLinkBonus, user.id) is not None:
        return True
    if _site_trial_time_to_skip(session, user, credential, now) > timedelta():
        return True
    if _has_trial_payment(session, YkPayment, user.id):
        return True
    if _has_trial_payment(session, YkRecurrentPayment, user.id):
        return True
    return credential is None and user.telegram_id is not None and user.telegram_id > 0


def _merge_single_user_site_row(
    session,
    model,
    source_user_id: int,
    target_user_id: int,
) -> None:
    source_row = session.get(model, source_user_id)
    if source_row is None:
        return

    target_row = session.get(model, target_user_id)
    if target_row is None:
        source_row.user_id = target_user_id
    else:
        if hasattr(target_row, "days_added"):
            target_row.days_added = max(
                target_row.days_added or 0,
                source_row.days_added or 0,
            )
        session.delete(source_row)


def _merge_single_user_row(
    session,
    model,
    source_user_id: int,
    target_user_id: int,
    *,
    merge_fields: tuple[str, ...] = (),
) -> None:
    source_row = session.execute(
        select(model).where(model.user_id == source_user_id)
    ).scalar_one_or_none()
    if source_row is None:
        return

    target_row = session.execute(
        select(model).where(model.user_id == target_user_id)
    ).scalar_one_or_none()
    if target_row is None:
        source_row.user_id = target_user_id
        return

    for field in merge_fields:
        setattr(
            target_row,
            field,
            bool(getattr(target_row, field)) or bool(getattr(source_row, field)),
        )
    session.delete(source_row)


def _merge_site_identities(
    session,
    source_user_id: int,
    target_user_id: int,
) -> None:
    for identity in session.execute(
        select(SiteIdentity).where(SiteIdentity.user_id == source_user_id)
    ).scalars():
        existing = session.get(SiteIdentity, identity.login)
        if existing is not None and existing.user_id == target_user_id:
            session.delete(identity)
        else:
            identity.user_id = target_user_id


def _merge_common_user_rows(
    session,
    source_user_id: int,
    target_user_id: int,
) -> None:
    (
        EventLog,
        ExpiredUsersNotification,
        ExtendSubscriptionNotification,
        NcUsersNotification,
        UserTrafficProgress,
    ) = _common_user_related_models()
    table_names = set(inspect(session.connection()).get_table_names())
    models = (
        EventLog,
        ExpiredUsersNotification,
        ExtendSubscriptionNotification,
        NcUsersNotification,
        UserTrafficProgress,
    )
    if not all(model.__tablename__ in table_names for model in models):
        return

    session.execute(
        update(EventLog)
        .where(EventLog.user_id == source_user_id)
        .values(user_id=target_user_id)
    )
    _merge_single_user_row(
        session,
        ExpiredUsersNotification,
        source_user_id,
        target_user_id,
    )
    _merge_single_user_row(
        session,
        ExtendSubscriptionNotification,
        source_user_id,
        target_user_id,
        merge_fields=("three_days_before", "one_day_before"),
    )
    _merge_single_user_row(
        session,
        NcUsersNotification,
        source_user_id,
        target_user_id,
    )
    _merge_single_user_row(
        session,
        UserTrafficProgress,
        source_user_id,
        target_user_id,
        merge_fields=("passed_0", "passed_5mb", "passed_100mb"),
    )


def _merge_recurrent_payment(
    session,
    model,
    source_user_id: int,
    target_user_id: int,
) -> bool:
    table_name = model.__tablename__
    source_recurrent = session.execute(
        text(
            f"SELECT id, captured_at FROM {table_name} "
            "WHERE user_id = :source LIMIT 1"
        ),
        {"source": source_user_id},
    ).first()
    if source_recurrent is None:
        return (
            session.execute(
                text(
                    f"SELECT id FROM {table_name} "
                    "WHERE user_id = :target LIMIT 1"
                ),
                {"target": target_user_id},
            ).first()
            is not None
        )

    target_recurrent = session.execute(
        text(
            f"SELECT id, captured_at FROM {table_name} "
            "WHERE user_id = :target LIMIT 1"
        ),
        {"target": target_user_id},
    ).first()
    if target_recurrent is None:
        session.execute(
            text(f"UPDATE {table_name} SET user_id = :target WHERE user_id = :source"),
            {"target": target_user_id, "source": source_user_id},
        )
        return True

    target_captured_at = target_recurrent.captured_at or datetime.min
    source_captured_at = source_recurrent.captured_at or datetime.min
    if source_captured_at >= target_captured_at:
        session.execute(
            text(f"DELETE FROM {table_name} WHERE user_id = :target"),
            {"target": target_user_id},
        )
        session.execute(
            text(f"UPDATE {table_name} SET user_id = :target WHERE user_id = :source"),
            {"target": target_user_id, "source": source_user_id},
        )
        return True

    session.execute(
        text(f"DELETE FROM {table_name} WHERE user_id = :source"),
        {"source": source_user_id},
    )
    return True


def _merge_referral_state(
    session,
    User,
    ReferralBonus,
    source_user,
    target_user,
) -> None:
    source_id = source_user.id
    target_id = target_user.id

    if target_user.referred_by_id == source_id:
        next_referrer_id = (
            source_user.referred_by_id
            if source_user.referred_by_id not in {source_id, target_id}
            else None
        )
        target_user.referred_by_id = next_referrer_id
        session.execute(
            update(User)
            .where(User.id == target_id)
            .values(referred_by_id=next_referrer_id)
        )
    elif (
        target_user.referred_by_id is None
        and source_user.referred_by_id not in {None, source_id, target_id}
    ):
        target_user.referred_by_id = source_user.referred_by_id
        session.execute(
            update(User)
            .where(User.id == target_id)
            .values(referred_by_id=source_user.referred_by_id)
        )

    session.execute(
        update(User)
        .where(User.referred_by_id == source_id, User.id != target_id)
        .values(referred_by_id=target_id)
    )

    for bonus in session.execute(
        select(ReferralBonus).where(ReferralBonus.referrer_id == source_id)
    ).scalars():
        if bonus.referral_id in {source_id, target_id}:
            session.delete(bonus)
        else:
            bonus.referrer_id = target_id

    session.flush()
    for bonus in session.execute(
        select(ReferralBonus).where(ReferralBonus.referral_id == source_id)
    ).scalars():
        if bonus.referrer_id in {source_id, target_id}:
            session.delete(bonus)
            continue

        existing = session.execute(
            select(ReferralBonus).where(
                ReferralBonus.referral_id == target_id,
                ReferralBonus.bonus_type == bonus.bonus_type,
            )
        ).scalar_one_or_none()
        if existing is None:
            bonus.referral_id = target_id
        else:
            existing.days_added = (existing.days_added or 0) + (bonus.days_added or 0)
            session.delete(bonus)


def _merged_expire_at(
    target_expire_at: datetime | None,
    source_expire_at: datetime | None,
    *,
    now: datetime,
    source_free_time_to_skip: timedelta,
    bonus_days_added: int,
) -> datetime:
    target_remaining = _remaining_time(target_expire_at, now)
    source_remaining = _remaining_time(source_expire_at, now)
    source_remaining = max(
        source_remaining - source_free_time_to_skip,
        timedelta(),
    )
    return now + target_remaining + source_remaining + timedelta(days=bonus_days_added)


def _add_or_update_site_identity(
    session,
    login: str | None,
    user_id: int,
    password_hash: str,
) -> None:
    if not login or not password_hash:
        return

    normalized_login = login.strip().lower()
    if not normalized_login:
        return

    identity = session.get(SiteIdentity, normalized_login)
    if identity is None:
        session.add(
            SiteIdentity(
                login=normalized_login,
                user_id=user_id,
                password_hash=password_hash,
            )
        )
    else:
        identity.user_id = user_id
        identity.password_hash = password_hash


def link_telegram_account(site_user_id: int, telegram_id: int) -> TelegramLinkResult:
    if telegram_id <= 0:
        raise ValueError("telegram_id must be positive")

    if not database_enabled():
        site_user = get_user_by_id(site_user_id)
        if site_user is None:
            raise ValueError("site user not found")
        if (
            site_user.telegram_id is not None
            and site_user.telegram_id > 0
            and site_user.telegram_id != telegram_id
        ):
            raise ValueError("site user is already linked to another Telegram account")
        already_linked = site_user.telegram_id == telegram_id
        if already_linked:
            return TelegramLinkResult(site_user, 0, False, True)
        bonus_days = 0
        if site_user.id not in _demo_telegram_link_bonuses:
            bonus_days = settings.telegram_link_bonus_days
            _demo_telegram_link_bonuses.add(site_user.id)
        expire_at = site_user.expire_at or datetime.now(timezone.utc)
        linked_user = SiteUser(
            id=site_user.id,
            username=site_user.username,
            display_name=site_user.display_name,
            expire_at=expire_at + timedelta(days=bonus_days),
            telegram_id=telegram_id,
        )
        _demo_users[linked_user.username] = linked_user
        return TelegramLinkResult(linked_user, bonus_days, False, already_linked)

    ReferralBonus, User, YkPayment, YkRecurrentPayment = _common_models()
    with _SessionLocal() as session:
        site_user = session.get(User, site_user_id)
        if site_user is None:
            raise ValueError("site user not found")
        if (
            site_user.telegram_id is not None
            and site_user.telegram_id > 0
            and site_user.telegram_id != telegram_id
        ):
            raise ValueError("site user is already linked to another Telegram account")

        telegram_user = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        target_user = site_user
        source_user = (
            telegram_user
            if telegram_user is not None and telegram_user.id != site_user.id
            else None
        )
        already_linked = telegram_user is not None and telegram_user.id == site_user.id
        if already_linked:
            return TelegramLinkResult(
                user=_site_user_from_db_user(target_user),
                bonus_days_added=0,
                merged_existing_telegram_user=False,
                already_linked=True,
            )

        site_login = (site_user.username or str(site_user.id)).strip().lower()
        source_original_username = (
            source_user.username or str(source_user.id) if source_user is not None else None
        )
        source_expire_at = source_user.expire_at if source_user is not None else None
        remnawave_username_to_disable = None
        source_credential = (
            session.get(SiteUserCredential, source_user.id)
            if source_user is not None
            else None
        )
        target_credential = session.get(SiteUserCredential, target_user.id)
        now = _utcnow_naive()
        source_free_time_to_skip = timedelta()
        free_period_used = _free_period_already_used(
            session,
            target_user,
            target_credential,
            YkPayment,
            YkRecurrentPayment,
            now,
        )
        if source_user is not None:
            free_period_used = free_period_used or _free_period_already_used(
                session,
                source_user,
                source_credential,
                YkPayment,
                YkRecurrentPayment,
                now,
            )
        password_hash = (
            target_credential.password_hash
            if target_credential is not None
            else source_credential.password_hash if source_credential is not None else ""
        )

        if password_hash:
            _add_or_update_site_identity(
                session,
                site_login,
                target_user.id,
                password_hash,
            )

        if source_user is not None:
            if not _has_any_payment(
                session,
                YkPayment,
                source_user.id,
            ) and not _has_any_payment(session, YkRecurrentPayment, source_user.id):
                source_free_time_to_skip = _remaining_time(source_user.expire_at, now)
            if source_original_username and source_original_username != site_login:
                remnawave_username_to_disable = source_original_username
                _add_or_update_site_identity(
                    session,
                    source_original_username,
                    target_user.id,
                    password_hash,
                )
            source_user.telegram_id = _synthetic_telegram_id(
                f"merged:{source_user.id}:{telegram_id}"
            )
            if source_user.username:
                source_user.username = generate_site_username()
            session.flush()

            session.execute(
                text("UPDATE yk_payments SET user_id = :target WHERE user_id = :source"),
                {"target": target_user.id, "source": source_user.id},
            )
            if _merge_recurrent_payment(
                session,
                YkRecurrentPayment,
                source_user.id,
                target_user.id,
            ):
                target_user.autopay_allow = True

            _merge_referral_state(session, User, ReferralBonus, source_user, target_user)

            _merge_common_user_rows(session, source_user.id, target_user.id)
            _merge_site_identities(session, source_user.id, target_user.id)
            _merge_single_user_site_row(
                session,
                SiteUserCredential,
                source_user.id,
                target_user.id,
            )
            _merge_single_user_site_row(session, SiteTrialGrant, source_user.id, target_user.id)
            _merge_single_user_site_row(session, TelegramLinkBonus, source_user.id, target_user.id)
            session.flush()
            session.delete(source_user)

        bonus = session.get(TelegramLinkBonus, target_user.id)
        bonus_days_added = 0
        if bonus is None and not free_period_used:
            bonus_days_added = settings.telegram_link_bonus_days
            session.add(
                TelegramLinkBonus(
                    user_id=target_user.id,
                    telegram_id=telegram_id,
                    days_added=bonus_days_added,
                )
            )
            free_period_used = True

        if source_user is not None:
            target_user.expire_at = _merged_expire_at(
                target_user.expire_at,
                source_expire_at,
                now=now,
                source_free_time_to_skip=source_free_time_to_skip,
                bonus_days_added=bonus_days_added,
            )
        else:
            base_expire_at = _naive_utc(target_user.expire_at)
            if base_expire_at is None or base_expire_at < now:
                base_expire_at = now
            target_user.expire_at = base_expire_at + timedelta(days=bonus_days_added)
        target_user.telegram_id = telegram_id

        if session.get(SiteUserCredential, target_user.id) is None and password_hash:
            session.add(
                SiteUserCredential(
                    user_id=target_user.id,
                    password_hash=password_hash,
                )
            )

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise
        session.refresh(target_user)
        return TelegramLinkResult(
            user=_site_user_from_db_user(target_user),
            bonus_days_added=bonus_days_added,
            merged_existing_telegram_user=source_user is not None,
            already_linked=already_linked,
            remnawave_username_to_disable=remnawave_username_to_disable,
        )


def get_referrals(user: SiteUser) -> list[ReferralRow]:
    if not database_enabled():
        return [
            ReferralRow("friend_one", "Оплатил подписку", 10),
            ReferralRow("friend_two", "Зарегистрирован", 0),
        ]

    ReferralBonus, User, _, _ = _common_models()
    with _SessionLocal() as session:
        referrals = session.execute(
            select(User).where(User.referred_by_id == user.id).order_by(User.id.desc())
        ).scalars()
        bonuses = dict(
            session.execute(
                select(ReferralBonus.referral_id, func.coalesce(func.sum(ReferralBonus.days_added), 0))
                .where(ReferralBonus.referrer_id == user.id)
                .group_by(ReferralBonus.referral_id)
            ).all()
        )

        rows: list[ReferralRow] = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for referral in referrals:
            is_active = referral.expire_at is not None and referral.expire_at > now
            rows.append(
                ReferralRow(
                    username=referral.username,
                    status="Активная подписка" if is_active else "Зарегистрирован",
                    bonus_days=int(bonuses.get(referral.id, 0) or 0),
                )
            )
        return rows
