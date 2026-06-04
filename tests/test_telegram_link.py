from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import repository
from app.repository import (
    SiteIdentity,
    SiteOAuthIdentity,
    SiteTrialGrant,
    SiteUserCredential,
    TelegramLinkBonus,
    authenticate_site_user,
    consume_pending_registration,
    create_oauth_user,
    create_pending_registration,
    create_telegram_user,
    get_user_by_oauth_identity,
    get_user_by_telegram_id,
    link_telegram_account,
    link_oauth_identity,
    verify_pending_registration_code,
)
from app.security import hash_password
from common.models.db import (
    Base,
    ReferralBonus,
    ReferralBonusType,
    User,
    YkPayment,
    YkRecurrentPayment,
)


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TelegramLinkRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[
                User.__table__,
                ReferralBonus.__table__,
                YkPayment.__table__,
                YkRecurrentPayment.__table__,
            ],
        )
        repository._SiteBase.metadata.create_all(self.engine)
        self.sessionmaker = sessionmaker(bind=self.engine)
        self.original_engine = repository._engine
        self.original_session = repository._SessionLocal
        repository._engine = self.engine
        repository._SessionLocal = self.sessionmaker

    def tearDown(self):
        repository._engine = self.original_engine
        repository._SessionLocal = self.original_session
        self.engine.dispose()

    def add_user(self, user_id, username, telegram_id, expire_at, referred_by_id=None):
        with self.sessionmaker() as session:
            session.add(
                User(
                    id=user_id,
                    username=username,
                    telegram_id=telegram_id,
                    expire_at=expire_at,
                    referred_by_id=referred_by_id,
                )
            )
            session.commit()

    def add_site_login(self, user_id, username):
        password_hash = hash_password("secret123")
        with self.sessionmaker() as session:
            session.add(SiteUserCredential(user_id=user_id, password_hash=password_hash))
            session.add(
                SiteIdentity(
                    login=username,
                    user_id=user_id,
                    password_hash=password_hash,
                )
            )
            session.commit()

    def test_rejects_rebinding_account_with_real_telegram_id(self):
        now = utcnow_naive()
        self.add_user(1, "site", 111, now + timedelta(days=10))
        self.add_site_login(1, "site")

        with self.assertRaises(ValueError):
            link_telegram_account(1, 222)

    def test_repeated_link_is_noop(self):
        now = utcnow_naive()
        self.add_user(1, "site", 111, now + timedelta(days=10))
        self.add_site_login(1, "site")

        result = link_telegram_account(1, 111)

        self.assertTrue(result.already_linked)
        self.assertEqual(result.bonus_days_added, 0)
        self.assertFalse(result.merged_existing_telegram_user)

    def test_merges_payments_and_keeps_newest_recurrent_payment(self):
        now = utcnow_naive().replace(microsecond=0)
        self.add_user(1, "site", -101, now + timedelta(days=40))
        self.add_user(2, "bot", 111, now + timedelta(days=20))
        self.add_site_login(1, "site")

        with self.sessionmaker() as session:
            session.add(
                SiteTrialGrant(
                    user_id=1,
                    days_added=7,
                    created_at=now - timedelta(days=2),
                )
            )
            session.add(
                YkPayment(
                    id=1,
                    user_id=1,
                    amount=100,
                    currency="RUB",
                    status="succeeded",
                    created_at=now,
                    payment_id="site-payment",
                    subscription_period="30d",
                )
            )
            session.add(
                YkPayment(
                    id=2,
                    user_id=2,
                    amount=200,
                    currency="RUB",
                    status="succeeded",
                    created_at=now,
                    payment_id="bot-payment",
                    subscription_period="30d",
                )
            )
            session.add(
                YkRecurrentPayment(
                    id=1,
                    user_id=1,
                    amount=300,
                    currency="RUB",
                    recurrent_payment_id="site-recurrent",
                    subscription_period="30d",
                    captured_at=now,
                )
            )
            session.add(
                YkRecurrentPayment(
                    id=2,
                    user_id=2,
                    amount=200,
                    currency="RUB",
                    recurrent_payment_id="bot-recurrent",
                    subscription_period="30d",
                    captured_at=now - timedelta(days=1),
                )
            )
            session.commit()

        result = link_telegram_account(1, 111)

        self.assertTrue(result.merged_existing_telegram_user)
        self.assertEqual(result.user.id, 1)
        self.assertEqual(result.user.telegram_id, 111)
        self.assertEqual(result.bonus_days_added, 0)
        self.assertEqual(result.user.username, "site")
        self.assertEqual(result.remnawave_username_to_disable, "bot")

        with self.sessionmaker() as session:
            payments = (
                session.execute(select(YkPayment).order_by(YkPayment.id))
                .scalars()
                .all()
            )
            recurrent = session.execute(select(YkRecurrentPayment)).scalar_one()
            target = session.get(User, 1)
            source = session.get(User, 2)
            identity = session.get(SiteIdentity, "site")
            bot_identity = session.get(SiteIdentity, "bot")

        self.assertEqual([payment.user_id for payment in payments], [1, 1])
        self.assertEqual(recurrent.user_id, 1)
        self.assertEqual(recurrent.recurrent_payment_id, "site-recurrent")
        self.assertGreater(target.expire_at, now + timedelta(days=59))
        self.assertLess(target.expire_at, now + timedelta(days=61))
        self.assertIsNone(source)
        self.assertEqual(target.username, "site")
        self.assertEqual(identity.user_id, 1)
        self.assertEqual(bot_identity.user_id, 1)

    def test_merges_referrals_without_self_references(self):
        now = utcnow_naive()
        self.add_user(1, "site", -101, now + timedelta(days=10), referred_by_id=3)
        self.add_user(2, "bot", 111, now + timedelta(days=10), referred_by_id=1)
        self.add_user(3, "referrer", 333, now + timedelta(days=10))
        self.add_site_login(1, "site")

        with self.sessionmaker() as session:
            session.add_all(
                [
                    ReferralBonus(
                        id=1,
                        referrer_id=1,
                        referral_id=2,
                        bonus_type=ReferralBonusType.REGISTRATION,
                        days_added=5,
                    ),
                    ReferralBonus(
                        id=2,
                        referrer_id=3,
                        referral_id=2,
                        bonus_type=ReferralBonusType.PURCHASE,
                        days_added=2,
                    ),
                    ReferralBonus(
                        id=3,
                        referrer_id=3,
                        referral_id=1,
                        bonus_type=ReferralBonusType.PURCHASE,
                        days_added=4,
                    ),
                ]
            )
            session.commit()

        link_telegram_account(1, 111)

        with self.sessionmaker() as session:
            target = session.get(User, 1)
            source = session.get(User, 2)
            bonuses = session.execute(select(ReferralBonus)).scalars().all()
            self_refs = [
                bonus
                for bonus in bonuses
                if bonus.referrer_id == bonus.referral_id
            ]
            purchase_bonus = session.execute(
                select(ReferralBonus).where(
                    ReferralBonus.referral_id == 1,
                    ReferralBonus.bonus_type == ReferralBonusType.PURCHASE,
                )
            ).scalar_one()

        self.assertEqual(target.referred_by_id, 3)
        self.assertIsNone(source)
        self.assertEqual(self_refs, [])
        self.assertEqual(purchase_bonus.days_added, 6)
        self.assertEqual(len(bonuses), 1)

    def test_site_only_trial_user_does_not_get_second_free_bonus(self):
        now = utcnow_naive()
        self.add_user(1, "site", -101, now + timedelta(days=7))
        self.add_site_login(1, "site")
        with self.sessionmaker() as session:
            session.add(SiteTrialGrant(user_id=1, days_added=7, created_at=now))
            session.commit()

        result = link_telegram_account(1, 111)

        self.assertEqual(result.user.id, 1)
        self.assertEqual(result.user.telegram_id, 111)
        self.assertEqual(result.bonus_days_added, 0)
        with self.sessionmaker() as session:
            self.assertIsNone(session.get(TelegramLinkBonus, 1))

    def test_bot_free_time_is_not_added_without_payments(self):
        now = utcnow_naive().replace(microsecond=0)
        self.add_user(1, "site", -101, now + timedelta(days=7))
        self.add_user(2, "bot", 111, now + timedelta(days=7))
        self.add_site_login(1, "site")
        with self.sessionmaker() as session:
            session.add(SiteTrialGrant(user_id=1, days_added=7, created_at=now))
            session.commit()

        result = link_telegram_account(1, 111)

        self.assertEqual(result.user.id, 1)
        self.assertEqual(result.user.telegram_id, 111)
        self.assertEqual(result.bonus_days_added, 0)
        with self.sessionmaker() as session:
            target = session.get(User, 1)
            source = session.get(User, 2)

        self.assertIsNone(source)
        self.assertGreater(target.expire_at, now + timedelta(days=6))
        self.assertLess(target.expire_at, now + timedelta(days=8))

    def test_creates_telegram_login_user(self):
        now = utcnow_naive().replace(microsecond=0)

        user = create_telegram_user(111, now + timedelta(days=7), "shredder_user")
        found = get_user_by_telegram_id(111)

        self.assertEqual(user.telegram_id, 111)
        self.assertEqual(user.username, "111")
        self.assertEqual(found.id, user.id)
        with self.sessionmaker() as session:
            db_user = session.get(User, user.id)
            trial_grant = session.get(SiteTrialGrant, user.id)

        self.assertEqual(db_user.telegram_username, "shredder_user")
        self.assertEqual(trial_grant.days_added, 7)

    def test_email_registration_uses_email_as_login(self):
        now = utcnow_naive().replace(microsecond=0)

        registration = create_pending_registration(
            None,
            "User@Example.COM",
            "secret123",
        )
        pending = verify_pending_registration_code(
            registration.token,
            registration.code,
        )
        user = consume_pending_registration(pending, now + timedelta(days=7))

        self.assertTrue(user.username.startswith("site_"))
        self.assertIsNone(authenticate_site_user(user.username, "secret123"))
        self.assertEqual(
            authenticate_site_user("user@example.com", "secret123").id,
            user.id,
        )
        with self.sessionmaker() as session:
            email_identity = session.get(SiteIdentity, "user@example.com")

        self.assertEqual(email_identity.user_id, user.id)

    def test_creates_yandex_oauth_only_user(self):
        now = utcnow_naive().replace(microsecond=0)

        user = create_oauth_user(
            "yandex",
            "ya-123",
            "User@Example.COM",
            "site_yandex",
            now + timedelta(days=7),
        )
        found = get_user_by_oauth_identity("yandex", "ya-123")

        self.assertEqual(found.id, user.id)
        self.assertIsNone(authenticate_site_user("user@example.com", "secret123"))
        with self.sessionmaker() as session:
            oauth_identity = session.get(SiteOAuthIdentity, "yandex:ya-123")
            credential = session.get(SiteUserCredential, user.id)
            trial_grant = session.get(SiteTrialGrant, user.id)

        self.assertEqual(oauth_identity.user_id, user.id)
        self.assertEqual(oauth_identity.email, "user@example.com")
        self.assertIsNone(credential)
        self.assertEqual(trial_grant.days_added, 7)

    def test_yandex_oauth_links_existing_email_user(self):
        now = utcnow_naive().replace(microsecond=0)
        registration = create_pending_registration(
            None,
            "user@example.com",
            "secret123",
        )
        pending = verify_pending_registration_code(
            registration.token,
            registration.code,
        )
        user = consume_pending_registration(pending, now + timedelta(days=7))

        linked_user = link_oauth_identity(
            user.id,
            "yandex",
            "ya-123",
            "USER@EXAMPLE.COM",
        )
        found = get_user_by_oauth_identity("yandex", "ya-123")

        self.assertEqual(linked_user.id, user.id)
        self.assertEqual(found.id, user.id)
        self.assertEqual(
            authenticate_site_user("user@example.com", "secret123").id,
            user.id,
        )
        with self.sessionmaker() as session:
            oauth_identity = session.get(SiteOAuthIdentity, "yandex:ya-123")

        self.assertEqual(oauth_identity.email, "user@example.com")

    def test_merged_account_can_login_by_telegram_id(self):
        now = utcnow_naive().replace(microsecond=0)
        self.add_user(1, "site", -101, now + timedelta(days=40))
        self.add_user(2, "bot", 111, now + timedelta(days=20))
        self.add_site_login(1, "site")

        result = link_telegram_account(1, 111)
        found = get_user_by_telegram_id(111)

        self.assertEqual(result.user.id, 1)
        self.assertEqual(found.id, 1)
        self.assertEqual(found.telegram_id, 111)


if __name__ == "__main__":
    unittest.main()
