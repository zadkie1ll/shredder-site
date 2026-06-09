import unittest
from types import SimpleNamespace
from unittest import mock
from urllib import parse

from app import yandex_oauth


class YandexOAuthTest(unittest.TestCase):
    def test_authorize_url_uses_registered_app_permissions(self):
        settings = SimpleNamespace(
            public_base_url="https://shredderpro.ru",
            yandex_oauth_client_id="client-id",
            yandex_oauth_client_secret="client-secret",
            yandex_oauth_scopes="login:info,login:email",
        )

        with mock.patch.object(yandex_oauth, "settings", settings):
            authorize_url = yandex_oauth.build_yandex_authorize_url("state-value")

        query = parse.parse_qs(parse.urlparse(authorize_url).query)
        self.assertNotIn("scope", query)
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://shredderpro.ru/auth/yandex/callback"],
        )


if __name__ == "__main__":
    unittest.main()
