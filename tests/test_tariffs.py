import unittest

from app.tariffs import SITE_TARIFF_IDS, get_tariff_by_id, get_tariffs


class SiteTariffsTest(unittest.TestCase):
    def test_only_public_site_tariffs_are_listed(self):
        self.assertEqual(
            {tariff["db_tariff_id"] for tariff in get_tariffs()},
            SITE_TARIFF_IDS,
        )

    def test_hidden_common_tariffs_cannot_be_purchased(self):
        for tariff_id in ("oneday", "threedays", "threemonths"):
            with self.subTest(tariff_id=tariff_id):
                with self.assertRaises(ValueError):
                    get_tariff_by_id(tariff_id)

    def test_public_tariff_can_be_resolved(self):
        self.assertEqual(get_tariff_by_id("month").db_tariff_id, "month")


if __name__ == "__main__":
    unittest.main()
