from __future__ import annotations

import asyncio
import re
from functools import partial

from yookassa import Configuration, Payment

from app.config import settings


PAYMENT_SERVICE_NAME = "Shredder VPN"
PAYMENT_SERVICE_NAME_PATTERN = re.compile(
    r"\b(?:monkey[-\s]?island|shredder\s*VPN|shredderVPN|shredder\s*VPS)\b\s*:?\s*",
    re.IGNORECASE,
)


def _build_payment_description(tariff) -> str:
    description = PAYMENT_SERVICE_NAME_PATTERN.sub("", tariff.description)
    description = re.sub(r"\s+", " ", description).strip(" :-")
    if not description:
        return PAYMENT_SERVICE_NAME
    return f"{PAYMENT_SERVICE_NAME} {description}"


def _create_payment_sync(tariff, username: str, telegram_id: int | None) -> str:
    if not settings.yookassa_shop_id or not settings.yookassa_secret:
        raise RuntimeError("YooKassa credentials are not configured.")

    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret

    payment_description = _build_payment_description(tariff)
    metadata = {
        "username": username,
        "subscription_period": tariff.db_tariff_id,
        "autopay": False,
        "trial_promotion": False,
        "from_trial": False,
    }
    if telegram_id is not None:
        metadata["telegram_id"] = telegram_id

    payload = {
        "save_payment_method": True,
        "amount": {
            "value": f"{tariff.price:.2f}",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": f"{settings.public_base_url}/cabinet",
        },
        "metadata": metadata,
        "capture": True,
        "description": payment_description,
    }

    if settings.receipt_email:
        payload["receipt"] = {
            "customer": {
                "email": settings.receipt_email,
            },
            "items": [
                {
                    "description": payment_description,
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{tariff.price:.2f}",
                        "currency": "RUB",
                    },
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                    "measure": "piece",
                }
            ],
        }

    payment = Payment.create(payload)

    return payment.confirmation.confirmation_url


async def create_payment_url(tariff, username: str, telegram_id: int | None) -> str:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(
            None,
            partial(
                _create_payment_sync,
                tariff=tariff,
                username=username,
                telegram_id=telegram_id,
            ),
        ),
        timeout=15.0,
    )
