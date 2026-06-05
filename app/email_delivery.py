from __future__ import annotations

import asyncio
from email.message import EmailMessage
import logging
import smtplib

from app.config import settings


def _send_registration_code_sync(email: str, code: str) -> None:
    if not settings.smtp_host:
        if settings.environment == "production":
            raise RuntimeError("SMTP is not configured.")
        logging.info("Registration code for %s: %s", email, code)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message["Subject"] = "Код регистрации Shredder"
    message.set_content(
        "\n".join(
            [
                "Твой код регистрации Shredder:",
                "",
                code,
                "",
                "Код действует 15 минут.",
            ]
        )
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_registration_code(email: str, code: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send_registration_code_sync, email, code)
