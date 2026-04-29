from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import settings


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


_engine = create_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None
_SessionLocal = sessionmaker(bind=_engine) if _engine is not None else None
_demo_users: dict[str, SiteUser] = {}


def database_enabled() -> bool:
    return _SessionLocal is not None


def _common_models():
    try:
        from common.models.db import ReferralBonus, User
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule is required when database access is enabled."
        ) from exc
    return ReferralBonus, User


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


def get_user_by_username(username: str) -> SiteUser | None:
    username = username.strip().lower()
    if not database_enabled():
        return _demo_user(username)

    _, User = _common_models()
    with _SessionLocal() as session:
        user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            return None
        return SiteUser(
            id=user.id,
            username=user.username,
            display_name=user.username,
            expire_at=user.expire_at,
            telegram_id=user.telegram_id,
        )


def create_user(username: str | None, expire_at: datetime | None) -> SiteUser:
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
        return user

    _, User = _common_models()
    with _SessionLocal() as session:
        existing_user = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if existing_user is not None:
            raise ValueError("username already exists")

        db_user = User(
            username=username,
            telegram_id=_synthetic_telegram_id(username),
            expire_at=expire_at.replace(tzinfo=None) if expire_at and expire_at.tzinfo else expire_at,
        )
        session.add(db_user)
        try:
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


def get_referrals(user: SiteUser) -> list[ReferralRow]:
    if not database_enabled():
        return [
            ReferralRow("friend_one", "Оплатил подписку", 10),
            ReferralRow("friend_two", "Зарегистрирован", 0),
        ]

    ReferralBonus, User = _common_models()
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
