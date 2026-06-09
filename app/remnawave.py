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
    active_internal_squads: tuple[str, ...]


def subscription_squads() -> list[str]:
    return settings.internal_squads_uuids


def _timestamp_to_datetime(value) -> datetime | None:
    if value is None:
        return None
    try:
        return value.ToDatetime()
    except AttributeError:
        return None


def _remnawave_user_from_response(response, fallback_expire_at: datetime | None = None) -> RemnawaveUser:
    return RemnawaveUser(
        username=response.username,
        subscription_url=response.subscription_url or None,
        expire_at=_timestamp_to_datetime(response.expire_at)
        if response.HasField("expire_at")
        else fallback_expire_at,
        status=str(response.status) if response.HasField("status") else None,
        active_internal_squads=tuple(
            squad.uuid for squad in getattr(response, "active_internal_squads", [])
        ),
    )


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
        return _remnawave_user_from_response(response)
    except Exception:
        logging.exception("Failed to read Remnawave user %s", username)
        return None
    finally:
        await client.close()


async def create_remnawave_user(
    username: str,
    telegram_id: int | None = None,
) -> RemnawaveUser | None:
    if not settings.remnawave_enabled:
        return RemnawaveUser(
            username=username,
            subscription_url=None,
            expire_at=datetime.now(timezone.utc)
            + timedelta(days=settings.trial_period_days),
            status="demo",
            active_internal_squads=(),
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
        request = proto.AddUserRequest(
            username=username,
            expire_at=expire_at,
            status=proto.UserStatus.ACTIVE,
            traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
            active_internal_squads=[*subscription_squads()],
            created_at=datetime.now(timezone.utc),
            description="created from shredder-site registration",
        )
        if telegram_id is not None:
            request.telegram_id = telegram_id

        response = await client.add_user(request)
        if response is None:
            fallback_response = await client.get_user_by_username(username)
            if fallback_response is None:
                return None
            logging.warning(
                "RWMS add_user returned no response for %s, but user was found afterwards",
                username,
            )
            return _remnawave_user_from_response(fallback_response, expire_at)
        return _remnawave_user_from_response(response, expire_at)
    except Exception:
        logging.exception("Failed to create Remnawave user %s", username)
        return None
    finally:
        await client.close()


async def update_remnawave_user_after_telegram_link(
    username: str,
    expire_at: datetime | None,
    telegram_id: int,
    username_to_disable: str | None = None,
) -> RemnawaveUser | None:
    if not settings.remnawave_enabled:
        return None

    try:
        from common.rwms_client import RwmsClient
        import proto.rwmanager_pb2 as proto
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule and generated proto package are required "
            "for Remnawave/RWMS access."
        ) from exc

    client = RwmsClient(settings.rwms_addr, settings.rwms_port)
    try:
        existing_user = await client.get_user_by_username(username)
        if existing_user is None:
            return None

        request = proto.UpdateUserRequest(
            uuid=existing_user.uuid,
            telegram_id=telegram_id,
            status=proto.UserStatus.ACTIVE,
            traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
        )
        required_squads = subscription_squads()
        if required_squads:
            request.active_internal_squads.extend(required_squads)
        if expire_at is not None:
            if expire_at.tzinfo is None:
                expire_at = expire_at.replace(tzinfo=timezone.utc)
            request.expire_at = expire_at

        response = await client.update_user(request)
        if response is None:
            return None
        if username_to_disable and username_to_disable != username:
            source_user = await client.get_user_by_username(username_to_disable)
            if source_user is not None:
                disable_request = proto.UpdateUserRequest(
                    uuid=source_user.uuid,
                    status=proto.UserStatus.DISABLED,
                    expire_at=datetime.now(timezone.utc),
                )
                if await client.update_user(disable_request) is None:
                    logging.warning(
                        "Failed to disable merged Remnawave user %s",
                        username_to_disable,
                    )
        return _remnawave_user_from_response(response, expire_at)
    except Exception:
        logging.exception("Failed to update Remnawave user %s after Telegram link", username)
        return None
    finally:
        await client.close()


async def ensure_remnawave_user_internal_squads(username: str) -> RemnawaveUser | None:
    required_squads = subscription_squads()
    if not settings.remnawave_enabled or not required_squads:
        return None

    try:
        from common.rwms_client import RwmsClient
        import proto.rwmanager_pb2 as proto
    except ImportError as exc:
        raise RuntimeError(
            "The shared common submodule and generated proto package are required "
            "for Remnawave/RWMS access."
        ) from exc

    client = RwmsClient(settings.rwms_addr, settings.rwms_port)
    try:
        existing_user = await client.get_user_by_username(username)
        if existing_user is None:
            return None

        current_squads = {
            squad.uuid for squad in getattr(existing_user, "active_internal_squads", [])
        }
        required_squad_set = set(required_squads)
        if required_squad_set.issubset(current_squads):
            return _remnawave_user_from_response(existing_user)

        response = await client.update_user(
            proto.UpdateUserRequest(
                uuid=existing_user.uuid,
                active_internal_squads=[*required_squads],
            )
        )
        if response is None:
            return None
        return _remnawave_user_from_response(response)
    except Exception:
        logging.exception("Failed to ensure Remnawave squads for user %s", username)
        return None
    finally:
        await client.close()
