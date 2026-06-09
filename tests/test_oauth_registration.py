import unittest
from types import SimpleNamespace
from unittest import mock

from app import main


class OAuthRegistrationValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_new_yandex_account_with_non_ru_email(self):
        request = SimpleNamespace(session={})
        remnawave_user = SimpleNamespace(expire_at=None)
        created_user = SimpleNamespace(id=43, username="site-yandex-user")

        with (
            mock.patch("app.main.get_user_by_oauth_identity", return_value=None),
            mock.patch("app.main.get_user_by_username", return_value=None),
            mock.patch(
                "app.main.create_remnawave_user",
                return_value=remnawave_user,
            ),
            mock.patch(
                "app.main.create_oauth_user",
                return_value=created_user,
            ) as create_oauth_user,
            mock.patch("app.main.pending_referrer_id", return_value=None),
        ):
            user = await main._login_oauth_user(
                request,
                "yandex",
                "ya-new",
                "user@example.com",
            )

        self.assertIs(user, created_user)
        create_oauth_user.assert_called_once()
        self.assertEqual(create_oauth_user.call_args.args[2], "user@example.com")

    async def test_existing_oauth_identity_with_non_ru_email_still_logs_in(self):
        request = SimpleNamespace(session={})
        existing_user = SimpleNamespace(id=42, username="existing-user")

        with mock.patch(
            "app.main.get_user_by_oauth_identity",
            return_value=existing_user,
        ):
            user = await main._login_oauth_user(
                request,
                "yandex",
                "ya-existing",
                "legacy@example.com",
            )

        self.assertIs(user, existing_user)
        self.assertEqual(request.session["user_id"], 42)


if __name__ == "__main__":
    unittest.main()
