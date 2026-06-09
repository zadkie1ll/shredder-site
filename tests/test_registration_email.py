import unittest

from app.repository import create_pending_registration
from app.repository import registration_email_is_valid


class RegistrationEmailValidationTest(unittest.TestCase):
    def test_accepts_valid_ru_addresses(self):
        for email in (
            "user@example.ru",
            "USER@SUB.EXAMPLE.RU",
            "first.last+tag@mail-server.ru",
        ):
            with self.subTest(email=email):
                self.assertTrue(registration_email_is_valid(email))

    def test_rejects_non_ru_or_malformed_addresses(self):
        for email in (
            "user@example.com",
            "user@example.ru.evil.com",
            "user@ru",
            "user@.ru",
            "user@-example.ru",
            "user@example-.ru",
            "user@example..ru",
            ".user@example.ru",
            "user..name@example.ru",
            "user@@example.ru",
            "user name@example.ru",
        ):
            with self.subTest(email=email):
                self.assertFalse(registration_email_is_valid(email))

    def test_pending_registration_rejects_non_ru_address(self):
        with self.assertRaisesRegex(ValueError, r"\.ru"):
            create_pending_registration(None, "user@example.com", "secret123")


if __name__ == "__main__":
    unittest.main()
