import hashlib
import hmac
from unittest import mock
import unittest

from app.security import verify_telegram_login


def signed_payload(bot_token: str, auth_date: int) -> dict[str, str]:
    payload = {
        "id": "123456",
        "first_name": "Test",
        "auth_date": str(auth_date),
    }
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(payload.items())
    )
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return payload


class TelegramLoginSecurityTest(unittest.TestCase):
    def test_accepts_recent_valid_payload(self):
        with mock.patch("app.security.time.time", return_value=1_000):
            self.assertTrue(
                verify_telegram_login(
                    signed_payload("token", 990),
                    "token",
                    max_age_seconds=60,
                )
            )

    def test_rejects_expired_payload(self):
        with mock.patch("app.security.time.time", return_value=1_000):
            self.assertFalse(
                verify_telegram_login(
                    signed_payload("token", 900),
                    "token",
                    max_age_seconds=60,
                )
            )

    def test_rejects_payload_too_far_in_future(self):
        with mock.patch("app.security.time.time", return_value=1_000):
            self.assertFalse(
                verify_telegram_login(
                    signed_payload("token", 1_031),
                    "token",
                    max_age_seconds=60,
                )
            )


if __name__ == "__main__":
    unittest.main()
