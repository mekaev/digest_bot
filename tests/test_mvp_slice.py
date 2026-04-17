import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from app.bot.handlers.start import MAIN_KEYBOARD, _build_channels_view, _build_help_text, _build_period_text
from app.config import get_settings
from app.db.base import Base
from app.db.models import Channel, Digest, DigestItem, Post, Subscription, TelegramLinkCode, Topic
from app.db.session import SessionLocal, configure_database
from app.ingestion.service import IngestionService
from app.ingestion.telegram_client import (
    ChannelValidationError,
    TelegramChannel,
    TelegramIngestionClient,
    TelegramMessage,
)
from app.services.catalog_service import CatalogService
from app.services.digest_service import DEFAULT_DIGEST_MAX_ITEMS, DIGEST_SYSTEM_PROMPT, DigestService
from app.services.subscription_service import SubscriptionService
from app.services.user_channel_service import UserChannelService
from app.services.user_service import (
    ALLOWED_DIGEST_WINDOW_DAYS,
    DEFAULT_DIGEST_WINDOW_DAYS,
    UserService,
)


class DummyLLM:
    def is_enabled(self) -> bool:
        return False


class CapturingLLM:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, object]] = []

    def is_enabled(self) -> bool:
        return True

    def generate(
        self,
        prompt: str,
        max_tokens: int = 300,
        temperature: float = 0.2,
        system_prompt: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system_prompt": system_prompt,
            }
        )
        return self.response_text


class SessionTestMixin:
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

    def create_user_added_channel(
        self,
        session,
        user_id: int,
        telegram_handle: str,
        title: str,
        enabled: bool = True,
    ) -> Channel:
        topic = session.scalar(select(Topic).where(Topic.slug == "user-added"))
        if topic is None:
            topic = Topic(
                slug="user-added",
                name="User Added",
                description="Hidden bucket",
            )
            session.add(topic)
            session.flush()

        channel = Channel(
            topic_id=topic.id,
            telegram_handle=telegram_handle,
            title=title,
            description="",
            is_active=True,
            is_user_added=True,
            added_by_user_id=user_id,
        )
        session.add(channel)
        session.flush()
        session.add(
            Subscription(
                user_id=user_id,
                channel_id=channel.id,
                enabled=enabled,
                frequency="daily",
            )
        )
        session.commit()
        session.refresh(channel)
        return channel


class FakeTelegramValidationClient:
    def __init__(
        self,
        result: TelegramChannel | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def validate_public_channel(
        self,
        channel_reference: str,
        allow_login: bool = False,
    ) -> TelegramChannel:
        self.calls.append((channel_reference, allow_login))
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("FakeTelegramValidationClient requires result or error")
        return self.result


class MVPSliceTests(SessionTestMixin, unittest.TestCase):
    def test_schema_bootstrap_creates_core_tables(self) -> None:
        _session, engine = self.make_session()
        inspector = inspect(engine)
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
        channel_columns = {column["name"] for column in inspector.get_columns("channels")}
        self.assertTrue({"is_user_added", "added_by_user_id"}.issubset(channel_columns))
        schedule_columns = {column["name"] for column in inspector.get_columns("digest_schedules")}
        self.assertIn("window_days", schedule_columns)
        post_columns = {column["name"] for column in inspector.get_columns("posts")}
        self.assertTrue(
            {"views_count", "reactions_count", "forwards_count", "comments_count"}.issubset(post_columns)
        )

    def test_link_code_is_reused_and_unknown_channel_is_rejected(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(telegram_user_id=42, username="tester", display_name="Tester")
        first = UserService(session).get_or_create_link_code(user.id)
        second = UserService(session).get_or_create_link_code(user.id)

        self.assertEqual(first.code, second.code)

        with self.assertRaises(ValueError):
            SubscriptionService(session).set_subscription(user.id, channel_id=999, enabled=True)

    def test_catalog_toggle_and_store_messages_skip_empty_duplicates_and_keep_metrics(self) -> None:
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
                    views_count=100,
                    reactions_count=10,
                    forwards_count=3,
                    comments_count=2,
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
        self.assertEqual(posts[0].views_count, 100)
        self.assertEqual(posts[0].reactions_count, 10)
        self.assertEqual(posts[0].forwards_count, 3)
        self.assertEqual(posts[0].comments_count, 2)

    def test_generate_digest_returns_empty_without_posts(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(telegram_user_id=200, username="empty", display_name="Empty")
        self.create_user_added_channel(session, user.id, "emptyfeed", "Empty Feed", enabled=True)

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)

        self.assertFalse(result.has_content)
        self.assertIsNone(result.digest)

    def test_generate_digest_creates_digest_and_items(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(telegram_user_id=300, username="digest", display_name="Digest User")
        channel = self.create_user_added_channel(session, user.id, "digestfeed", "Digest Feed", enabled=True)

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
        digest_items = list(session.scalars(select(DigestItem).order_by(DigestItem.id.asc())))

        self.assertTrue(result.has_content)
        self.assertIsNotNone(result.digest)
        self.assertIn("Краткий дайджест по вашим каналам:", result.message_text)
        self.assertGreaterEqual(len(digest_items), 1)


class UserAddedChannelTests(SessionTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_add_public_channel_creates_user_added_channel_and_subscription(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=501,
            username="adder",
            display_name="Adder",
        )
        client = FakeTelegramValidationClient(
            result=TelegramChannel(
                telegram_handle="publicsource",
                title="Public Source",
                description="",
            )
        )

        result = await UserChannelService(session, client=client).add_public_channel_for_user(
            user.id,
            "@PublicSource",
        )

        stored_channel = session.scalar(
            select(Channel).where(Channel.telegram_handle == "publicsource")
        )
        stored_subscription = session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.channel_id == stored_channel.id,
            )
        )

        self.assertEqual(client.calls, [("@PublicSource", False)])
        self.assertTrue(result.channel_created)
        self.assertTrue(result.subscription_created)
        self.assertIsNotNone(stored_channel)
        self.assertTrue(stored_channel.is_user_added)
        self.assertEqual(stored_channel.added_by_user_id, user.id)
        self.assertIsNotNone(stored_subscription)
        self.assertTrue(stored_subscription.enabled)

    async def test_add_public_channel_rejects_invalid_input(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=502,
            username="invalid",
            display_name="Invalid Input",
        )

        with self.assertRaisesRegex(ChannelValidationError, "Send a public channel"):
            await UserChannelService(
                session,
                client=TelegramIngestionClient(),
            ).add_public_channel_for_user(user.id, "not-a-valid-channel")

        self.assertEqual(list(session.scalars(select(Channel))), [])
        self.assertEqual(list(session.scalars(select(Subscription))), [])

    async def test_add_public_channel_reuses_existing_channel_without_duplication(self) -> None:
        session, _engine = self.make_session()
        catalog_service = CatalogService(session)
        catalog_service.seed_catalog()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=503,
            username="duplicate",
            display_name="Duplicate Check",
        )
        curated_channel = catalog_service.list_channels()[0]
        client = FakeTelegramValidationClient(
            result=TelegramChannel(
                telegram_handle=curated_channel.telegram_handle,
                title=curated_channel.title,
                description=curated_channel.description,
            )
        )

        result = await UserChannelService(session, client=client).add_public_channel_for_user(
            user.id,
            f"@{curated_channel.telegram_handle}",
        )

        matching_channels = list(
            session.scalars(
                select(Channel).where(Channel.telegram_handle == curated_channel.telegram_handle)
            )
        )
        subscription = session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.channel_id == curated_channel.id,
            )
        )

        self.assertFalse(result.channel_created)
        self.assertEqual(len(matching_channels), 1)
        self.assertFalse(matching_channels[0].is_user_added)
        self.assertIsNone(matching_channels[0].added_by_user_id)
        self.assertIsNotNone(subscription)
        self.assertTrue(subscription.enabled)

    async def test_add_public_channel_rejects_private_or_unavailable_channel(self) -> None:
        cases = [
            ChannelValidationError("Private channels are not supported. Add a public channel instead."),
            ChannelValidationError("Channel @missingchannel was not found or is unavailable."),
        ]

        for index, error in enumerate(cases, start=1):
            with self.subTest(case=index):
                session, _engine = self.make_session()
                user = UserService(session).upsert_telegram_user(
                    telegram_user_id=510 + index,
                    username=f"user{index}",
                    display_name=f"User {index}",
                )
                service = UserChannelService(
                    session,
                    client=FakeTelegramValidationClient(error=error),
                )

                with self.assertRaisesRegex(ChannelValidationError, error.args[0]):
                    await service.add_public_channel_for_user(user.id, "@missingchannel")

                self.assertEqual(list(session.scalars(select(Channel))), [])
                self.assertEqual(list(session.scalars(select(Subscription))), [])


class BotUXTests(SessionTestMixin, unittest.TestCase):
    def test_channels_view_shows_only_user_added_channels(self) -> None:
        session, _engine = self.make_session()
        catalog_service = CatalogService(session)
        catalog_service.seed_catalog()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=601,
            username="botux",
            display_name="Bot UX",
        )
        curated_channel = catalog_service.list_channels()[0]
        SubscriptionService(session).set_subscription(user.id, curated_channel.id, enabled=True)
        self.create_user_added_channel(session, user.id, "mynewsfeed", "My News Feed", enabled=False)

        text, markup = _build_channels_view(session, user.id)
        button_texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertIn("Your channels:", text)
        self.assertIn("- OFF My News Feed (@mynewsfeed)", text)
        self.assertNotIn(curated_channel.title, text)
        self.assertNotIn(curated_channel.title, " ".join(button_texts))
        self.assertIn("[OFF] My News Feed", button_texts)
        self.assertIn("Remove", button_texts)

    def test_user_added_channel_toggle_and_remove_paths_are_reflected_in_view(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=602,
            username="toggleuser",
            display_name="Toggle User",
        )
        user_channel = self.create_user_added_channel(session, user.id, "signalnews", "Signal News", enabled=True)

        UserChannelService(session).toggle_user_channel(user.id, user_channel.id)
        text_after_toggle, _markup = _build_channels_view(session, user.id)
        self.assertIn("- OFF Signal News (@signalnews)", text_after_toggle)

        UserChannelService(session).remove_user_added_channel_for_user(user.id, user_channel.id)
        text_after_remove, _markup = _build_channels_view(session, user.id)
        self.assertNotIn("Signal News", text_after_remove)
        self.assertIn("Use /addchannel", text_after_remove)

    def test_topics_are_hidden_and_period_is_visible_in_help_and_keyboard(self) -> None:
        keyboard_texts = [button.text for row in MAIN_KEYBOARD.keyboard for button in row]

        self.assertNotIn("Topics", keyboard_texts)
        self.assertNotIn("/topics", _build_help_text())
        self.assertIn("Period", keyboard_texts)
        self.assertIn("/period", _build_help_text())
        self.assertNotIn("curated", _build_help_text().lower())

    def test_period_choices_are_saved(self) -> None:
        session, _engine = self.make_session()
        user_service = UserService(session)
        user = user_service.upsert_telegram_user(
            telegram_user_id=603,
            username="perioduser",
            display_name="Period User",
        )

        self.assertEqual(user_service.get_digest_window_days(user.id), DEFAULT_DIGEST_WINDOW_DAYS)
        for days in ALLOWED_DIGEST_WINDOW_DAYS:
            with self.subTest(days=days):
                schedule = user_service.set_digest_window_days(user.id, days)
                self.assertEqual(schedule.window_days, days)
                self.assertEqual(user_service.get_digest_window_days(user.id), days)
                self.assertIn(str(days), _build_period_text(days))


class DigestPromptTests(SessionTestMixin, unittest.TestCase):
    def test_digest_prompt_uses_russian_contract_and_top_n_limit(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=700,
            username="digestprompt",
            display_name="Digest Prompt",
        )
        channel = self.create_user_added_channel(session, user.id, "promptfeed", "Prompt Feed", enabled=True)

        now = datetime.now(timezone.utc)
        for message_id in range(1, DEFAULT_DIGEST_MAX_ITEMS + 3):
            session.add(
                Post(
                    channel_id=channel.id,
                    telegram_message_id=message_id,
                    raw_text=f"Update {message_id}",
                    cleaned_text=f"Product update {message_id}. Second sentence for summary stability.",
                    source_url=f"https://t.me/test/{message_id}",
                    published_at=now - timedelta(minutes=message_id),
                )
            )
        session.commit()

        llm = CapturingLLM(
            "Краткий дайджест по вашим каналам:\n\n"
            "1. Канал: Test\n"
            "Кратко: Короткое русскоязычное summary.\n"
            "Source: https://t.me/test/1\n\n"
            "2. Канал: Test\n"
            "Кратко: Еще один короткий итог.\n"
            "Source: https://t.me/test/2\n\n"
            "3. Канал: Test\n"
            "Кратко: Третий пункт.\n"
            "Source: https://t.me/test/3\n\n"
            "4. Канал: Test\n"
            "Кратко: Четвертый пункт.\n"
            "Source: https://t.me/test/4\n\n"
            "5. Канал: Test\n"
            "Кратко: Пятый пункт.\n"
            "Source: https://t.me/test/5"
        )

        result = DigestService(session, llm=llm).generate_digest_for_user(user.id)

        self.assertTrue(result.has_content)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["system_prompt"], DIGEST_SYSTEM_PROMPT)
        self.assertIn("итоговый текст должен быть только на русском языке", llm.calls[0]["prompt"])
        self.assertIn("не использовать китайский язык", llm.calls[0]["prompt"])
        self.assertIn("Source: <ссылка>", llm.calls[0]["prompt"])
        self.assertIn(f"Используй только {DEFAULT_DIGEST_MAX_ITEMS} лучших материалов.", llm.calls[0]["prompt"])
        self.assertEqual(result.digest.source_post_count, DEFAULT_DIGEST_MAX_ITEMS)

    def test_digest_falls_back_when_llm_returns_cjk_text(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=701,
            username="digestfallback",
            display_name="Digest Fallback",
        )
        channel = self.create_user_added_channel(session, user.id, "fallbackfeed", "Fallback Feed", enabled=True)
        session.add(
            Post(
                channel_id=channel.id,
                telegram_message_id=91,
                raw_text="Release notes",
                cleaned_text="Release notes with enough content for a stable fallback summary.",
                source_url="https://t.me/test/91",
                published_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

        result = DigestService(session, llm=CapturingLLM("你好，世界")).generate_digest_for_user(user.id)

        self.assertIn("Краткий дайджест по вашим каналам:", result.message_text)
        self.assertNotIn("你好", result.message_text)
        self.assertIn("Source: https://t.me/test/91", result.message_text)

    def test_digest_filters_posts_by_saved_window(self) -> None:
        session, _engine = self.make_session()
        user_service = UserService(session)
        user = user_service.upsert_telegram_user(
            telegram_user_id=702,
            username="windowuser",
            display_name="Window User",
        )
        user_service.set_digest_window_days(user.id, 1)
        channel = self.create_user_added_channel(session, user.id, "windowfeed", "Window Feed", enabled=True)

        now = datetime.now(timezone.utc)
        session.add(
            Post(
                channel_id=channel.id,
                telegram_message_id=201,
                raw_text="Recent update",
                cleaned_text="Recent update inside the selected window.",
                source_url="https://t.me/test/201",
                published_at=now - timedelta(hours=6),
            )
        )
        session.add(
            Post(
                channel_id=channel.id,
                telegram_message_id=202,
                raw_text="Old update",
                cleaned_text="Old update outside the selected window.",
                source_url="https://t.me/test/202",
                published_at=now - timedelta(days=4),
            )
        )
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)

        self.assertTrue(result.has_content)
        self.assertIn("https://t.me/test/201", result.message_text)
        self.assertNotIn("https://t.me/test/202", result.message_text)
        self.assertEqual(result.digest.source_post_count, 1)

    def test_ranking_handles_missing_metrics_and_prefers_higher_signal_posts(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=703,
            username="rankuser",
            display_name="Rank User",
        )
        channel = self.create_user_added_channel(session, user.id, "rankfeed", "Rank Feed", enabled=True)

        now = datetime.now(timezone.utc)
        low_signal_post = Post(
            channel_id=channel.id,
            telegram_message_id=301,
            raw_text="Low signal",
            cleaned_text="Low signal update with missing metrics.",
            source_url="https://t.me/test/301",
            published_at=now - timedelta(hours=2),
        )
        high_signal_post = Post(
            channel_id=channel.id,
            telegram_message_id=302,
            raw_text="High signal",
            cleaned_text="High signal update with strong engagement.",
            source_url="https://t.me/test/302",
            views_count=1200,
            reactions_count=180,
            forwards_count=45,
            comments_count=30,
            published_at=now - timedelta(hours=3),
        )
        session.add(low_signal_post)
        session.add(high_signal_post)
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)
        digest_items = list(session.scalars(select(DigestItem).order_by(DigestItem.id.asc())))

        self.assertTrue(result.has_content)
        self.assertGreaterEqual(len(digest_items), 2)
        self.assertEqual(digest_items[0].post_id, high_signal_post.id)

    def test_top_n_truncation_works(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=704,
            username="topnuser",
            display_name="Top N User",
        )
        channel = self.create_user_added_channel(session, user.id, "topnfeed", "Top N Feed", enabled=True)

        now = datetime.now(timezone.utc)
        for message_id in range(1, DEFAULT_DIGEST_MAX_ITEMS + 4):
            session.add(
                Post(
                    channel_id=channel.id,
                    telegram_message_id=400 + message_id,
                    raw_text=f"Post {message_id}",
                    cleaned_text=f"Digest candidate {message_id} with enough text for ranking.",
                    source_url=f"https://t.me/test/{400 + message_id}",
                    views_count=message_id * 100,
                    published_at=now - timedelta(hours=message_id),
                )
            )
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)
        digest_items = list(session.scalars(select(DigestItem).order_by(DigestItem.id.asc())))

        self.assertTrue(result.has_content)
        self.assertEqual(result.digest.source_post_count, DEFAULT_DIGEST_MAX_ITEMS)
        self.assertEqual(len(digest_items), DEFAULT_DIGEST_MAX_ITEMS)


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
