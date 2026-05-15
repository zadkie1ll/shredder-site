import os
from dataclasses import dataclass


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _postgres_url_from_env() -> str | None:
    explicit_url = os.getenv("SHREDDER_SITE_DATABASE_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("MI_VPN_BOT_POSTGRES_HOST")
    port = os.getenv("MI_VPN_BOT_POSTGRES_PORT")
    user = os.getenv("MI_VPN_BOT_POSTGRES_USER")
    password = os.getenv("MI_VPN_BOT_POSTGRES_PASSWORD")
    db = os.getenv("MI_VPN_BOT_POSTGRES_DB")
    if not all([host, port, user, password, db]):
        return None

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


@dataclass(frozen=True)
class Settings:
    environment: str
    session_secret: str
    login_password: str
    database_url: str | None
    rwms_addr: str | None
    rwms_port: int | None
    public_base_url: str
    trial_period_days: int
    internal_squads_uuids: list[str]
    yookassa_shop_id: str | None
    yookassa_secret: str | None
    receipt_email: str
    telegram_bot_username: str | None
    telegram_bot_token: str | None
    telegram_link_bonus_days: int
    telegram_login_max_age_seconds: int
    one_click_redirect_url: str | None

    @property
    def remnawave_enabled(self) -> bool:
        return bool(self.rwms_addr and self.rwms_port)


def load_settings() -> Settings:
    environment = os.getenv("SHREDDER_SITE_ENV", "development")
    session_secret = os.getenv("SHREDDER_SITE_SESSION_SECRET", "dev-only-change-me")
    login_password = os.getenv("SHREDDER_SITE_LOGIN_PASSWORD", "demo12345")
    yookassa_shop_id = os.getenv("SHREDDER_SITE_YOOKASSA_SHOP_ID") or os.getenv(
        "MI_VPN_BOT_SHOP_ID"
    )
    yookassa_secret = os.getenv("SHREDDER_SITE_YOOKASSA_SECRET") or os.getenv(
        "MI_VPN_BOT_SECRET"
    )
    rwms_addr = os.getenv("SHREDDER_SITE_RWMS_ADDR") or os.getenv("MI_VPN_BOT_RWMS_ADDR")
    rwms_port_raw = os.getenv("SHREDDER_SITE_RWMS_PORT") or os.getenv("MI_VPN_BOT_RWMS_PORT")
    rwms_port = int(rwms_port_raw) if rwms_port_raw else None
    squads_value = os.getenv("SHREDDER_SITE_INTERNAL_SQUADS_UUIDS") or os.getenv(
        "MI_VPN_BOT_INTERNAL_SQUADS_UUIDS", ""
    )
    one_click_redirect_url = os.getenv("SHREDDER_SITE_ONE_CLICK_REDIRECT_URL") or os.getenv(
        "MI_VPN_BOT_REDIRECT_URL"
    )

    database_url = _postgres_url_from_env()
    if environment == "production":
        if session_secret == "dev-only-change-me":
            raise ValueError("SHREDDER_SITE_SESSION_SECRET must be set in production.")
        if not database_url:
            raise ValueError("Postgres connection envs must be set in production.")
        if not rwms_addr or not rwms_port:
            raise ValueError("RWMS/Remnawave envs must be set in production.")
        if not yookassa_shop_id or not yookassa_secret:
            raise ValueError("YooKassa envs must be set in production.")

    return Settings(
        environment=environment,
        session_secret=session_secret,
        login_password=login_password,
        database_url=database_url,
        rwms_addr=rwms_addr,
        rwms_port=rwms_port,
        public_base_url=os.getenv("SHREDDER_SITE_PUBLIC_BASE_URL", "https://shredder.local"),
        trial_period_days=_read_int("SHREDDER_SITE_TRIAL_PERIOD_DAYS", 7),
        internal_squads_uuids=[
            squad_uuid.strip()
            for squad_uuid in squads_value.split(",")
            if squad_uuid.strip()
        ],
        yookassa_shop_id=yookassa_shop_id,
        yookassa_secret=yookassa_secret,
        receipt_email=os.getenv("SHREDDER_SITE_RECEIPT_EMAIL", "receipts@orpheous.ru"),
        telegram_bot_username=os.getenv("SHREDDER_SITE_TELEGRAM_BOT_USERNAME"),
        telegram_bot_token=os.getenv("SHREDDER_SITE_TELEGRAM_BOT_TOKEN")
        or os.getenv("MI_VPN_BOT_TOKEN"),
        telegram_link_bonus_days=_read_int("SHREDDER_SITE_TELEGRAM_LINK_BONUS_DAYS", 7),
        telegram_login_max_age_seconds=_read_int(
            "SHREDDER_SITE_TELEGRAM_LOGIN_MAX_AGE_SECONDS",
            86400,
        ),
        one_click_redirect_url=one_click_redirect_url,
    )


settings = load_settings()
