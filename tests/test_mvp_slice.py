import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from app.config import get_settings
from app.db.base import Base
from app.db.models import Channel, Digest, DigestItem, Post, Subscription, TelegramLinkCode
from app.db.session import SessionLocal, configure_database
from app.ingestion.service import IngestionService
from app.ingestion.telegram_client import TelegramMessage
from app.services.catalog_service import CatalogService
from app.services.digest_service import DigestService
from app.services.subscription_service import SubscriptionService
from app.services.user_service import UserService


class DummyLLM:
    def is_enabled(self) -> bool:
        return False


class MVPSliceTests(unittest.TestCase):
    def make_session(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(
            bind=engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        session = session_factory()
        self.addCleanup(engine.dispose)
        self.addCleanup(session.close)
        return session, engine

    def test_schema_bootstrap_creates_core_tables(self) -> None:
        _session, engine = self.make_session()
        tables = set(inspect(engine).get_table_names())
        expected = {
            "users",
            "telegram_link_codes",
            "topics",
            "channels",
            "subscriptions",
            "digest_schedules",
            "posts",
            "ingestion_runs",
            "digests",
            "digest_items",
        }
        self.assertTrue(expected.issubset(tables))

    def test_link_code_is_reused_and_unknown_channel_is_rejected(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(telegram_user_id=42, username="tester", display_name="Tester")
        first = UserService(session).get_or_create_link_code(user.id)
        second = UserService(session).get_or_create_link_code(user.id)

        self.assertEqual(first.code, second.code)

        with self.assertRaises(ValueError):
            SubscriptionService(session).set_subscription(user.id, channel_id=999, enabled=True)

    def test_catalog_toggle_and_store_messages_skip_empty_and_duplicates(self) -> None:
        session, _engine = self.make_session()
        catalog_service = CatalogService(session)
        catalog_service.seed_catalog()
        user = UserService(session).upsert_telegram_user(telegram_user_id=100, username="reader", display_name="Reader")
        channel = catalog_service.list_channels()[0]

        subscription = SubscriptionService(session).toggle_subscription(user.id, channel.id)
        self.assertTrue(subscription.enabled)

        stored_count = IngestionService(session).store_messages(
            channel,
            [
                TelegramMessage(
                    telegram_message_id=1,
                    raw_text="First post",
                    cleaned_text="First post",
                    channel_handle=channel.telegram_handle,
                    published_at=datetime.now(timezone.utc),
                    source_url="https://t.me/test/1",
                ),
                TelegramMessage(
                    telegram_message_id=1,
                    raw_text="First post",
                    cleaned_text="First post",
                    channel_handle=channel.telegram_handle,
                    published_at=datetime.now(timezone.utc),
                    source_url="https://t.me/test/1",
                ),
                TelegramMessage(
                    telegram_message_id=2,
                    raw_text="   ",
                    cleaned_text="",
                    channel_handle=channel.telegram_handle,
                    published_at=datetime.now(timezone.utc),
                    source_url="https://t.me/test/2",
                ),
            ],
        )

        posts = list(session.scalars(select(Post)))
        self.assertEqual(stored_count, 1)
        self.assertEqual(len(posts), 1)

    def test_generate_digest_returns_empty_without_posts(self) -> None:
        session, _engine = self.make_session()
        catalog_service = CatalogService(session)
        catalog_service.seed_catalog()
        user = UserService(session).upsert_telegram_user(telegram_user_id=200, username="empty", display_name="Empty")
        channel = catalog_service.list_channels()[0]
        SubscriptionService(session).set_subscription(user.id, channel.id, enabled=True)

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)

        self.assertFalse(result.has_content)
        self.assertIsNone(result.digest)

    def test_generate_digest_creates_digest_and_items(self) -> None:
        session, _engine = self.make_session()
        catalog_service = CatalogService(session)
        catalog_service.seed_catalog()
        user = UserService(session).upsert_telegram_user(telegram_user_id=300, username="digest", display_name="Digest User")
        channel = catalog_service.list_channels()[0]
        SubscriptionService(session).set_subscription(user.id, channel.id, enabled=True)

        now = datetime.now(timezone.utc)
        session.add(
            Post(
                channel_id=channel.id,
                telegram_message_id=10,
                raw_text="Major product update shipped today",
                cleaned_text="Major product update shipped today with several improvements",
                source_url="https://t.me/test/10",
                published_at=now - timedelta(hours=1),
            )
        )
        session.add(
            Post(
                channel_id=channel.id,
                telegram_message_id=11,
                raw_text="Older note",
                cleaned_text="Older note about maintenance",
                source_url="https://t.me/test/11",
                published_at=now - timedelta(hours=5),
            )
        )
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)
        digest_items = list(session.scalars(select(DigestItem)))

        self.assertTrue(result.has_content)
        self.assertIsNotNone(result.digest)
        self.assertIn("Your latest digest:", result.message_text)
        self.assertGreaterEqual(len(digest_items), 1)


class WebSurfaceTests(unittest.TestCase):
    def make_web_client(self) -> TestClient:
        default_database_url = get_settings().database_url
        data_dir = Path(__file__).resolve().parents[1] / "data"
        db_fd, db_path = tempfile.mkstemp(prefix="web-surface-", suffix=".db", dir=data_dir)
        os.close(db_fd)

        configure_database(f"sqlite:///{Path(db_path).as_posix()}")
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        self.addCleanup(lambda: configure_database(default_database_url))

        from app.api.main import create_app

        client = TestClient(create_app())
        self.addCleanup(client.close)
        return client

    def create_user_with_code(
        self,
        telegram_user_id: int,
        username: str,
        display_name: str,
    ) -> tuple[int, int, str]:
        with SessionLocal() as session:
            service = UserService(session)
            user = service.upsert_telegram_user(
                telegram_user_id=telegram_user_id,
                username=username,
                display_name=display_name,
            )
            link_code = service.get_or_create_link_code(user.id)
            return user.id, link_code.id, link_code.code

    def redeem_code(
        self,
        client: TestClient,
        code: str,
        next_path: str = "/app",
        follow_redirects: bool = False,
    ):
        return client.post(
            "/auth/redeem",
            data={"code": code, "next": next_path},
            follow_redirects=follow_redirects,
        )

    def test_landing_page_loads(self) -> None:
        client = self.make_web_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Curated Telegram digests", response.text)

    def test_protected_pages_redirect_without_session(self) -> None:
        client = self.make_web_client()

        for path in ("/app", "/app/digests", "/app/subscriptions"):
            with self.subTest(path=path):
                response = client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers["location"].startswith("/login?next="))

    def test_valid_link_code_redeem_sets_session_and_marks_code_used(self) -> None:
        client = self.make_web_client()
        _user_id, link_code_id, link_code = self.create_user_with_code(
            telegram_user_id=401,
            username="webuser",
            display_name="Web User",
        )

        response = self.redeem_code(client, link_code)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/app")
        self.assertIn("session=", response.headers.get("set-cookie", ""))

        with SessionLocal() as session:
            redeemed_code = session.get(TelegramLinkCode, link_code_id)
            self.assertIsNotNone(redeemed_code.used_at)

    def test_invalid_expired_and_used_codes_render_login_error(self) -> None:
        client = self.make_web_client()
        _user_id, expired_code_id, expired_code = self.create_user_with_code(
            telegram_user_id=402,
            username="expired",
            display_name="Expired Code",
        )
        _other_user_id, used_code_id, used_code = self.create_user_with_code(
            telegram_user_id=403,
            username="used",
            display_name="Used Code",
        )

        with SessionLocal() as session:
            expired = session.get(TelegramLinkCode, expired_code_id)
            used = session.get(TelegramLinkCode, used_code_id)
            expired.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            used.used_at = datetime.now(timezone.utc)
            session.commit()

        cases = [
            ("MISSING00", "Link code not found."),
            (expired_code, "This link code has expired."),
            (used_code, "This link code was already used."),
        ]
        for code, message in cases:
            with self.subTest(code=code):
                response = self.redeem_code(client, code)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.text)
                self.assertNotIn("session=", response.headers.get("set-cookie", ""))

    def test_profile_page_shows_current_user(self) -> None:
        client = self.make_web_client()
        user_id, _code_id, link_code = self.create_user_with_code(
            telegram_user_id=404,
            username="profile",
            display_name="Profile User",
        )

        with SessionLocal() as session:
            channel = CatalogService(session).list_channels()[0]
            SubscriptionService(session).set_subscription(user_id, channel.id, enabled=True)
            session.add(
                Digest(
                    user_id=user_id,
                    status="ready",
                    delivery_status="sent",
                    body_text="Profile digest body",
                    source_post_count=2,
                )
            )
            session.commit()

        self.redeem_code(client, link_code)
        response = client.get("/app")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Profile User", response.text)
        self.assertIn("Profile digest body", response.text)

    def test_digests_page_shows_only_current_user_digests(self) -> None:
        client = self.make_web_client()
        user_id, _code_id, link_code = self.create_user_with_code(
            telegram_user_id=405,
            username="digestviewer",
            display_name="Digest Viewer",
        )
        other_user_id, _other_code_id, _other_code = self.create_user_with_code(
            telegram_user_id=406,
            username="otheruser",
            display_name="Other User",
        )

        with SessionLocal() as session:
            session.add(
                Digest(
                    user_id=user_id,
                    status="ready",
                    delivery_status="sent",
                    body_text="Visible digest for current user",
                    source_post_count=3,
                )
            )
            session.add(
                Digest(
                    user_id=other_user_id,
                    status="ready",
                    delivery_status="sent",
                    body_text="Hidden digest for other user",
                    source_post_count=4,
                )
            )
            session.commit()

        self.redeem_code(client, link_code)
        response = client.get("/app/digests")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Visible digest for current user", response.text)
        self.assertNotIn("Hidden digest for other user", response.text)

    def test_subscriptions_page_renders_and_updates_user_subscriptions(self) -> None:
        client = self.make_web_client()
        user_id, _code_id, link_code = self.create_user_with_code(
            telegram_user_id=407,
            username="subviewer",
            display_name="Subscription Viewer",
        )

        with SessionLocal() as session:
            channel = CatalogService(session).list_channels()[0]
            channel_id = channel.id
            channel_title = channel.title

        self.redeem_code(client, link_code)
        initial_response = client.get("/app/subscriptions")
        self.assertEqual(initial_response.status_code, 200)
        self.assertIn(channel_title, initial_response.text)
        self.assertIn("Enable", initial_response.text)

        response = client.post(
            f"/app/subscriptions/{channel_id}",
            data={"enabled": "true"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(channel_title, response.text)
        self.assertIn("Disable", response.text)

        with SessionLocal() as session:
            subscription = session.scalar(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.channel_id == channel_id,
                )
            )
            self.assertIsNotNone(subscription)
            self.assertTrue(subscription.enabled)


if __name__ == "__main__":
    unittest.main()
