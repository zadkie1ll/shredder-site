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

    def test_uses_payment_all_nodes_squad_as_fallback(self):
        with mock.patch.dict(
            os.environ,
            {
                "MI_YKP_INTERNAL_ALL_NODES_SQUAD_UUID": "all-nodes",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.internal_squads_uuids, ["all-nodes"])

    def test_site_squads_override_shared_service_fallbacks(self):
        with mock.patch.dict(
            os.environ,
            {
                "SHREDDER_SITE_INTERNAL_SQUADS_UUIDS": "site-a, site-b",
                "MI_VPN_BOT_INTERNAL_SQUADS_UUIDS": "bot-squad",
                "MI_YKP_INTERNAL_ALL_NODES_SQUAD_UUID": "payment-squad",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.internal_squads_uuids, ["site-a", "site-b"])


if __name__ == "__main__":
    unittest.main()
