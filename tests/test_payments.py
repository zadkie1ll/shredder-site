import unittest

from app.payments import _linked_telegram_id


class PaymentTelegramIdTest(unittest.TestCase):
    def test_keeps_bot_telegram_id(self):
        self.assertEqual(_linked_telegram_id(123456), 123456)

    def test_omits_site_synthetic_telegram_id(self):
        self.assertIsNone(_linked_telegram_id(-123456))

    def test_keeps_missing_telegram_id_missing(self):
        self.assertIsNone(_linked_telegram_id(None))


if __name__ == "__main__":
    unittest.main()
