from datetime import timedelta


SITE_TARIFF_IDS = frozenset({"month", "sixmonths", "year"})


def _period_to_text(period: timedelta) -> str:
    days = period.days
    if days == 1:
        return "1 день"
    if days == 3:
        return "3 дня"
    if days == 30:
        return "30 дней"
    if days == 90:
        return "90 дней"
    if days == 180:
        return "180 дней"
    if days == 360:
        return "360 дней"
    return f"{days} дней"


def get_tariffs() -> list[dict]:
    try:
        from common.models.tariff import ALL_TARIFFS, tariff_to_human_str
    except ImportError:
        return [
            {"name": "1 месяц", "price": 249, "period": "30 дней", "highlight": False},
            {"name": "6 месяцев", "price": 999, "period": "180 дней", "highlight": False},
            {"name": "1 год", "price": 1799, "period": "360 дней", "highlight": False},
        ]

    tariffs = []
    for tariff in ALL_TARIFFS:
        if tariff.db_tariff_id not in SITE_TARIFF_IDS:
            continue
        name = tariff_to_human_str(tariff) or _period_to_text(tariff.subscription_period)
        tariffs.append(
            {
                "name": name,
                "price": tariff.price,
                "period": _period_to_text(tariff.subscription_period),
                "db_tariff_id": tariff.db_tariff_id,
                "highlight": False,
            }
        )
    return tariffs


def get_tariff_by_id(db_tariff_id: str):
    if db_tariff_id not in SITE_TARIFF_IDS:
        raise ValueError(f"Unknown site tariff id: {db_tariff_id}")

    try:
        from common.models.tariff import str_to_tariff
    except ImportError as exc:
        raise RuntimeError("common.models.tariff is required for payments") from exc

    tariff = str_to_tariff(db_tariff_id)
    if tariff is None:
        raise ValueError(f"Unknown tariff id: {db_tariff_id}")
    return tariff
