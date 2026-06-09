import unittest
from unittest import mock
from pathlib import Path

from app import main


class FakeSession(dict):
    pass


class FakeRequest:
    def __init__(self):
        self.session = FakeSession()


class AuthContextTest(unittest.TestCase):
    @mock.patch("app.main.current_user", return_value=None)
    @mock.patch("app.main.get_pending_registration", return_value=None)
    def test_register_context_contains_social_auth_settings(
        self,
        _get_pending_registration,
        _current_user,
    ):
        context = main.register_context(FakeRequest())

        self.assertIn("telegram_bot_username", context)
        self.assertIn("telegram_auth_url", context)
        self.assertIn("google_oauth_enabled", context)
        self.assertIn("yandex_oauth_enabled", context)
        self.assertIn("yandex_client_id", context)
        self.assertIn("yandex_origin", context)
        self.assertIn("yandex_token_uri", context)

    def test_yandex_button_has_server_side_fallback(self):
        template = Path("app/templates/_social_auth.html").read_text()

        self.assertIn('href="/auth/yandex/start"', template)


if __name__ == "__main__":
    unittest.main()
