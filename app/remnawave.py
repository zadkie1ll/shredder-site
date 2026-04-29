from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import settings


@dataclass(frozen=True)
class RemnawaveUser:
    username: str
    subscription_url: str | None
    expire_at: datetime | None
    status: str | None


def _timestamp_to_datetime(value) -> datetime | None:
    if value is None:
        return None
    try:
        return value.ToDatetime()
    except AttributeError:
        return None


async def get_remnawave_user(username: str) -> RemnawaveUser | None:
    if not settings.remnawave_enabled:
        return None

    try:
        from common.rwms_client import RwmsClient
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule and generated proto package are required "
            "for Remnawave/RWMS access."
        ) from exc

    client = RwmsClient(settings.rwms_addr, settings.rwms_port)
    try:
        response = await client.get_user_by_username(username)
        if response is None:
            return None
        return RemnawaveUser(
            username=response.username,
            subscription_url=response.subscription_url or None,
            expire_at=_timestamp_to_datetime(response.expire_at)
            if response.HasField("expire_at")
            else None,
            status=str(response.status) if response.HasField("status") else None,
        )
    except Exception:
        logging.exception("Failed to read Remnawave user %s", username)
        return None
    finally:
        await client.close()


async def create_remnawave_user(username: str) -> RemnawaveUser | None:
    if not settings.remnawave_enabled:
        return RemnawaveUser(
            username=username,
            subscription_url=None,
            expire_at=datetime.now(timezone.utc)
            + timedelta(days=settings.trial_period_days),
            status="demo",
        )

    try:
        from common.rwms_client import RwmsClient
        import proto.rwmanager_pb2 as proto
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule and generated proto package are required "
            "for Remnawave/RWMS access."
        ) from exc

    client = RwmsClient(settings.rwms_addr, settings.rwms_port)
    expire_at = datetime.now(timezone.utc) + timedelta(days=settings.trial_period_days)
    try:
        response = await client.add_user(
            proto.AddUserRequest(
                username=username,
                expire_at=expire_at,
                status=proto.UserStatus.ACTIVE,
                traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
                active_internal_squads=[*settings.internal_squads_uuids],
                created_at=datetime.now(timezone.utc),
                description="created from shredder-site registration",
            )
        )
        if response is None:
            return None
        return RemnawaveUser(
            username=response.username,
            subscription_url=response.subscription_url or None,
            expire_at=_timestamp_to_datetime(response.expire_at)
            if response.HasField("expire_at")
            else expire_at,
            status=str(response.status) if response.HasField("status") else None,
        )
    except Exception:
        logging.exception("Failed to create Remnawave user %s", username)
        return None
    finally:
        await client.close()
