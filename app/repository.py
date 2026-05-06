from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, create_engine, func, select, update
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


_engine = create_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None
_SessionLocal = sessionmaker(bind=_engine) if _engine is not None else None
_demo_users: dict[str, SiteUser] = {}
_demo_password_hashes: dict[str, str] = {}
_demo_telegram_link_bonuses: set[int] = set()
_SiteBase = declarative_base()


class SiteUserCredential(_SiteBase):
    __tablename__ = "site_user_credentials"

    user_id = Column(BigInteger, primary_key=True)
    password_hash = Column(String(256), nullable=False)


class SiteIdentity(_SiteBase):
    __tablename__ = "site_identities"

    login = Column(String(256), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TelegramLinkBonus(_SiteBase):
    __tablename__ = "site_telegram_link_bonuses"

    user_id = Column(BigInteger, primary_key=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    days_added = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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


def _synthetic_telegram_id(username: str) -> int:
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return -int(digest[:15], 16)


def generate_site_username() -> str:
    return f"site_{secrets.token_hex(8)}"


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
    username = username.strip().lower()
    if not database_enabled():
        return _demo_user(username)

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        identity = session.get(SiteIdentity, username)
        if identity is not None:
            user = session.get(User, identity.user_id)
            if user is not None:
                return _site_user_from_db_user(user)

        user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            return None
        return _site_user_from_db_user(user)


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
        credential = session.get(SiteUserCredential, user.id)
        if credential is None or not verify_password(password, credential.password_hash):
            return None
        return _site_user_from_db_user(user)


def create_user(username: str | None, expire_at: datetime | None, password: str) -> SiteUser:
    username = (username or generate_site_username()).strip().lower()

    if not database_enabled():
        user = SiteUser(
            id=len(_demo_users) + 1,
            username=username,
            display_name=username,
            expire_at=expire_at,
            telegram_id=_synthetic_telegram_id(username),
        )
        _demo_users[username] = user
        _demo_password_hashes[username] = hash_password(password)
        return user

    _, User, _, _ = _common_models()
    with _SessionLocal() as session:
        existing_user = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        existing_identity = session.get(SiteIdentity, username)
        if existing_user is not None or existing_identity is not None:
            raise ValueError("username already exists")

        password_hash = hash_password(password)
        db_user = User(
            username=username,
            telegram_id=_synthetic_telegram_id(username),
            expire_at=expire_at.replace(tzinfo=None) if expire_at and expire_at.tzinfo else expire_at,
        )
        session.add(db_user)
        try:
            session.flush()
            session.add(
                SiteUserCredential(
                    user_id=db_user.id,
                    password_hash=password_hash,
                )
            )
            session.add(
                SiteIdentity(
                    login=username,
                    user_id=db_user.id,
                    password_hash=password_hash,
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("username already exists") from exc
        session.refresh(db_user)
        return SiteUser(
            id=db_user.id,
            username=db_user.username or str(db_user.id),
            display_name=db_user.username or str(db_user.id),
            expire_at=db_user.expire_at,
            telegram_id=db_user.telegram_id,
        )


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


def _max_expire_at(*values: datetime | None) -> datetime | None:
    normalized = [
        value.replace(tzinfo=None) if value is not None and value.tzinfo else value
        for value in values
        if value is not None
    ]
    if not normalized:
        return None
    return max(normalized)


def link_telegram_account(site_user_id: int, telegram_id: int) -> TelegramLinkResult:
    if not database_enabled():
        site_user = get_user_by_id(site_user_id)
        if site_user is None:
            raise ValueError("site user not found")
        already_linked = site_user.telegram_id == telegram_id
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

        telegram_user = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        target_user = telegram_user or site_user
        source_user = site_user if telegram_user is not None and telegram_user.id != site_user.id else None
        already_linked = telegram_user is not None and telegram_user.id == site_user.id

        site_login = (site_user.username or str(site_user.id)).strip().lower()
        source_credential = session.get(SiteUserCredential, site_user.id)
        target_credential = session.get(SiteUserCredential, target_user.id)
        password_hash = (
            source_credential.password_hash
            if source_credential is not None
            else target_credential.password_hash if target_credential is not None else ""
        )

        if password_hash:
            identity = session.get(SiteIdentity, site_login)
            if identity is None:
                session.add(
                    SiteIdentity(
                        login=site_login,
                        user_id=target_user.id,
                        password_hash=password_hash,
                    )
                )
            else:
                identity.user_id = target_user.id
                identity.password_hash = password_hash

        if source_user is not None:
            session.execute(
                update(YkPayment)
                .where(YkPayment.user_id == source_user.id)
                .values(user_id=target_user.id)
            )
            target_recurrent = session.execute(
                select(YkRecurrentPayment).where(YkRecurrentPayment.user_id == target_user.id)
            ).scalar_one_or_none()
            source_recurrent = session.execute(
                select(YkRecurrentPayment).where(YkRecurrentPayment.user_id == source_user.id)
            ).scalar_one_or_none()
            if target_recurrent is None:
                session.execute(
                    update(YkRecurrentPayment)
                    .where(YkRecurrentPayment.user_id == source_user.id)
                    .values(user_id=target_user.id)
                )
            elif source_recurrent is not None:
                target_captured_at = target_recurrent.captured_at or datetime.min
                source_captured_at = source_recurrent.captured_at or datetime.min
                if source_captured_at >= target_captured_at:
                    target_recurrent.recurrent_payment_id = source_recurrent.recurrent_payment_id
                    target_recurrent.currency = source_recurrent.currency
                    target_recurrent.amount = source_recurrent.amount
                    target_recurrent.subscription_period = source_recurrent.subscription_period
                    target_recurrent.captured_at = source_recurrent.captured_at
                    target_recurrent.is_trial_promotion = source_recurrent.is_trial_promotion
                    target_recurrent.scheduled_payment = source_recurrent.scheduled_payment
                session.delete(source_recurrent)

            if target_user.referred_by_id is None and source_user.referred_by_id != target_user.id:
                target_user.referred_by_id = source_user.referred_by_id
            session.execute(
                update(User)
                .where(User.referred_by_id == source_user.id)
                .values(referred_by_id=target_user.id)
            )
            for bonus in session.execute(
                select(ReferralBonus).where(ReferralBonus.referrer_id == source_user.id)
            ).scalars():
                bonus.referrer_id = target_user.id
            for bonus in session.execute(
                select(ReferralBonus).where(ReferralBonus.referral_id == source_user.id)
            ).scalars():
                existing = session.execute(
                    select(ReferralBonus).where(
                        ReferralBonus.referral_id == target_user.id,
                        ReferralBonus.bonus_type == bonus.bonus_type,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    bonus.referral_id = target_user.id
                else:
                    existing.days_added = (existing.days_added or 0) + (bonus.days_added or 0)
                    session.delete(bonus)

            source_user.telegram_id = _synthetic_telegram_id(
                f"merged:{source_user.id}:{telegram_id}"
            )

        combined_expire_at = _max_expire_at(target_user.expire_at, site_user.expire_at)
        bonus = session.get(TelegramLinkBonus, target_user.id)
        bonus_days_added = 0
        if bonus is None:
            bonus_days_added = settings.telegram_link_bonus_days
            session.add(
                TelegramLinkBonus(
                    user_id=target_user.id,
                    telegram_id=telegram_id,
                    days_added=bonus_days_added,
                )
            )

        if combined_expire_at is None or combined_expire_at < datetime.utcnow():
            combined_expire_at = datetime.utcnow()
        target_user.expire_at = combined_expire_at + timedelta(days=bonus_days_added)
        target_user.telegram_id = telegram_id

        if target_credential is None and password_hash:
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
