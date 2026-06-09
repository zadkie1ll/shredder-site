import unittest
from types import SimpleNamespace
from unittest import mock

from app import remnawave


class FakeRwmsClient:
    last_request = None

    def __init__(self, _address, _port):
        pass

    async def add_user(self, request):
        self.__class__.last_request = request
        return None

    async def get_user_by_username(self, _username):
        return None

    async def close(self):
        pass


class RemnawaveConfigTest(unittest.IsolatedAsyncioTestCase):
    def test_subscription_squads_are_not_gated_by_legacy_flag(self):
        settings = SimpleNamespace(
            internal_squads_uuids=["all-nodes"],
        )

        with mock.patch.object(remnawave, "settings", settings):
            squads = remnawave.subscription_squads()

        self.assertEqual(squads, ["all-nodes"])

    async def test_create_user_sends_configured_squads_to_rwms(self):
        settings = SimpleNamespace(
            remnawave_enabled=True,
            rwms_addr="rwms",
            rwms_port=50051,
            trial_period_days=7,
            internal_squads_uuids=["all-nodes"],
        )

        with (
            mock.patch.object(remnawave, "settings", settings),
            mock.patch("common.rwms_client.RwmsClient", FakeRwmsClient),
        ):
            await remnawave.create_remnawave_user("site-user")

        self.assertIsNotNone(FakeRwmsClient.last_request)
        self.assertEqual(
            list(FakeRwmsClient.last_request.active_internal_squads),
            ["all-nodes"],
        )


if __name__ == "__main__":
    unittest.main()
