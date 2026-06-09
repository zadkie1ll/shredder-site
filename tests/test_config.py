import os
import unittest
from unittest import mock

from app.config import load_settings


class SettingsTest(unittest.TestCase):
    def test_public_base_url_trailing_slash_is_removed(self):
        with mock.patch.dict(
            os.environ,
            {
                "SHREDDER_SITE_PUBLIC_BASE_URL": "https://site.example/",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.public_base_url, "https://site.example")


if __name__ == "__main__":
    unittest.main()
