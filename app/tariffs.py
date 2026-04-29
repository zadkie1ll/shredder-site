from datetime import timedelta


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
            {"name": "3 месяца", "price": 599, "period": "90 дней", "highlight": True},
            {"name": "6 месяцев", "price": 999, "period": "180 дней", "highlight": False},
            {"name": "1 год", "price": 1799, "period": "360 дней", "highlight": False},
        ]

    tariffs = []
    for tariff in ALL_TARIFFS:
        if tariff.subscription_period.days < 30:
            continue
        name = tariff_to_human_str(tariff) or _period_to_text(tariff.subscription_period)
        tariffs.append(
            {
                "name": name,
                "price": tariff.price,
                "period": _period_to_text(tariff.subscription_period),
                "highlight": tariff.subscription_period.days == 90,
            }
        )
    return tariffs
