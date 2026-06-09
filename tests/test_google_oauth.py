import unittest
from types import SimpleNamespace
from unittest import mock

from app import google_oauth


class GoogleOAuthTest(unittest.TestCase):
    def test_redirect_uri_uses_normalized_public_base_url(self):
        with mock.patch.object(
            google_oauth,
            "settings",
            SimpleNamespace(public_base_url="https://shredderpro.ru"),
        ):
            redirect_uri = google_oauth.google_redirect_uri()

        self.assertEqual(
            redirect_uri,
            "https://shredderpro.ru/auth/google/callback",
        )


if __name__ == "__main__":
    unittest.main()
