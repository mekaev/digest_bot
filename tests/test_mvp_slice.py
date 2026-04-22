import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from app.bot.handlers import start as bot_start
from app.bot.handlers.start import (
    MAIN_KEYBOARD,
    _build_channels_view,
    _build_help_text,
    _build_period_text,
)
from app.config import get_settings
from app.db.base import Base
from app.db.models import Channel, Digest, DigestItem, Post, Subscription, TelegramLinkCode, Topic
from app.db.session import SessionLocal, configure_database
from app.ingestion.service import IngestionService
from app.ingestion.telegram_client import (
    ChannelValidationError,
    IngestionConfigurationError,
    TelegramChannel,
    TelegramIngestionClient,
    TelegramMessage,
    normalize_channel_reference,
)
from app.rag.qa import QAResponse, QASource, QAService
from app.services.stt import STTConfigurationError, STTService
from app.services.catalog_service import CatalogService
from app.services.digest_service import DEFAULT_DIGEST_MAX_ITEMS, DIGEST_SYSTEM_PROMPT, DigestService
from app.services.subscription_service import SubscriptionService
from app.services.user_channel_service import AddChannelResult, UserChannelService
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


class DummyFromUser:
    def __init__(self, user_id: int, username: str, full_name: str) -> None:
        self.id = user_id
        self.username = username
        self.full_name = full_name


class DummyVoice:
    def __init__(self, file_id: str = "voice-file-id", file_size: int = 1024) -> None:
        self.file_id = file_id
        self.file_size = file_size


class DummyMessage:
    def __init__(
        self,
        text: str | None,
        user_id: int = 9001,
        username: str = "botuser",
        full_name: str = "Bot User",
        voice: DummyVoice | None = None,
    ) -> None:
        self.text = text
        self.from_user = DummyFromUser(user_id=user_id, username=username, full_name=full_name)
        self.voice = voice
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append(
            {
                "text": text,
                "reply_markup": reply_markup,
            }
        )


class DummyFSMContext:
    def __init__(self) -> None:
        self.cleared = False

    async def clear(self) -> None:
        self.cleared = True


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

    async def test_add_public_channel_accepts_t_me_url(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=504,
            username="urladder",
            display_name="URL Adder",
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
            "https://t.me/PublicSource",
        )

        self.assertTrue(result.channel_created)
        self.assertEqual(client.calls, [("https://t.me/PublicSource", False)])
        self.assertEqual(result.channel.telegram_handle, "publicsource")

    def test_normalize_channel_reference_supports_public_handles_and_t_me_links(self) -> None:
        self.assertEqual(normalize_channel_reference("@PublicSource"), "publicsource")
        self.assertEqual(normalize_channel_reference("https://t.me/PublicSource"), "publicsource")
        self.assertEqual(normalize_channel_reference("https://www.t.me/PublicSource"), "publicsource")

    async def test_validate_public_channel_wraps_runtime_value_error_as_config_error(self) -> None:
        class BrokenTelethonClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def connect(self) -> None:
                raise ValueError("too many values to unpack (expected 5)")

        client = TelegramIngestionClient()

        with patch.object(client, "is_configured", return_value=True):
            with patch("app.ingestion.telegram_client.TelegramClient", new=BrokenTelethonClient):
                with self.assertRaisesRegex(IngestionConfigurationError, "refresh the local session"):
                    await client.validate_public_channel("@publicsource")

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


class STTServiceTests(unittest.TestCase):
    def make_audio_file(self) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix="stt-test-", suffix=".ogg")
        os.close(fd)
        path = Path(raw_path)
        path.write_bytes(b"fake audio")
        self.addCleanup(lambda: path.exists() and path.unlink())
        return path

    def test_stt_service_requires_api_key(self) -> None:
        audio_path = self.make_audio_file()
        service = STTService(api_key="", api_base_url="https://stt.example/v1")

        with self.assertRaisesRegex(STTConfigurationError, "STT is not configured"):
            service.transcribe(audio_path)

    def test_stt_service_parses_whisper_compatible_response(self) -> None:
        audio_path = self.make_audio_file()
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"text": "  hello   world  "}'

        def fake_urlopen(request, timeout: int):
            calls.append((request, timeout))
            return FakeResponse()

        service = STTService(
            api_key="test-key",
            api_base_url="https://stt.example/v1",
            model="whisper-test",
            language="ru",
            timeout_seconds=12,
        )

        with patch("app.services.stt.urlopen", new=fake_urlopen):
            transcript = service.transcribe(audio_path)

        self.assertEqual(transcript, "hello world")
        self.assertEqual(calls[0][0].full_url, "https://stt.example/v1/audio/transcriptions")
        self.assertEqual(calls[0][1], 12)

    def test_stt_service_builds_together_transcription_request(self) -> None:
        audio_path = self.make_audio_file()
        calls = []

        class FakeTranscriptionResponse:
            text = "together transcript"

        class FakeTranscriptions:
            def create(self, **kwargs):
                calls.append(("create", kwargs))
                return FakeTranscriptionResponse()

        class FakeAudio:
            def __init__(self) -> None:
                self.transcriptions = FakeTranscriptions()

        class FakeTogether:
            def __init__(self, **kwargs) -> None:
                calls.append(("client", kwargs))
                self.audio = FakeAudio()

            def close(self) -> None:
                calls.append(("close", {}))

        service = STTService(
            api_key="tgp_test_key",
            api_base_url="https://api.together.ai/v1",
            model="openai/whisper-large-v3",
            language="ru",
        )

        with patch("app.services.stt.Together", new=FakeTogether):
            transcript = service.transcribe(audio_path)

        client_call = calls[0][1]
        create_call = calls[1][1]

        self.assertEqual(transcript, "together transcript")
        self.assertEqual(client_call["api_key"], "tgp_test_key")
        self.assertEqual(client_call["base_url"], "https://api.together.ai/v1")
        self.assertEqual(Path(create_call["file"].name), audio_path)
        self.assertFalse(isinstance(create_call["file"], str))
        self.assertEqual(create_call["model"], "openai/whisper-large-v3")
        self.assertEqual(create_call["language"], "ru")
        self.assertEqual(create_call["response_format"], "json")
        self.assertEqual(calls[-1][0], "close")

    def test_stt_service_routes_together_key_away_from_openai_default(self) -> None:
        service = STTService(
            api_key="tgp_test_key",
            api_base_url="https://api.openai.com/v1",
            model="openai/whisper-large-v3",
        )

        self.assertEqual(service.api_base_url, "https://api.together.ai/v1")


class BotAddChannelFlowTests(SessionTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_handle_add_channel_submission_succeeds_for_at_username_without_crash(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage("@publicsource")
        state = DummyFSMContext()
        call_log: list[str] = []

        async def fake_add_public_channel_for_user(self, user_id: int, channel_reference: str, allow_login: bool = False):
            call_log.append(channel_reference)
            return AddChannelResult(
                channel=Channel(
                    id=1,
                    topic_id=1,
                    telegram_handle="publicsource",
                    title="Public Source",
                    description="",
                    is_active=True,
                    is_user_added=True,
                    added_by_user_id=user_id,
                ),
                subscription=Subscription(
                    id=1,
                    user_id=user_id,
                    channel_id=1,
                    enabled=True,
                    frequency="daily",
                ),
                channel_created=True,
                subscription_created=True,
                already_enabled=False,
            )

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(UserChannelService, "add_public_channel_for_user", new=fake_add_public_channel_for_user):
                await bot_start._handle_add_channel_submission(message, state, "@publicsource")

        self.assertEqual(call_log, ["@publicsource"])
        self.assertTrue(state.cleared)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("@publicsource", message.answers[0]["text"])
        self.assertIn("added and enabled", message.answers[0]["text"])

    async def test_handle_add_channel_submission_succeeds_for_t_me_url_without_crash(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage("https://t.me/publicsource")
        state = DummyFSMContext()
        call_log: list[str] = []

        async def fake_add_public_channel_for_user(self, user_id: int, channel_reference: str, allow_login: bool = False):
            call_log.append(channel_reference)
            return AddChannelResult(
                channel=Channel(
                    id=2,
                    topic_id=1,
                    telegram_handle="publicsource",
                    title="Public Source",
                    description="",
                    is_active=True,
                    is_user_added=True,
                    added_by_user_id=user_id,
                ),
                subscription=Subscription(
                    id=2,
                    user_id=user_id,
                    channel_id=2,
                    enabled=True,
                    frequency="daily",
                ),
                channel_created=True,
                subscription_created=True,
                already_enabled=False,
            )

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(UserChannelService, "add_public_channel_for_user", new=fake_add_public_channel_for_user):
                await bot_start._handle_add_channel_submission(message, state, "https://t.me/publicsource")

        self.assertEqual(call_log, ["https://t.me/publicsource"])
        self.assertTrue(state.cleared)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("@publicsource", message.answers[0]["text"])

    async def test_handle_add_channel_submission_returns_validation_error_without_crash(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage("bad channel")
        state = DummyFSMContext()

        async def fake_add_public_channel_for_user(self, user_id: int, channel_reference: str, allow_login: bool = False):
            raise ChannelValidationError("Send a valid public channel as @username or https://t.me/username.")

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(UserChannelService, "add_public_channel_for_user", new=fake_add_public_channel_for_user):
                await bot_start._handle_add_channel_submission(message, state, "bad channel")

        self.assertFalse(state.cleared)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("Send a valid public channel", message.answers[0]["text"])

    async def test_handle_add_channel_submission_returns_runtime_config_error_without_crash(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage("@publicsource")
        state = DummyFSMContext()

        async def fake_add_public_channel_for_user(self, user_id: int, channel_reference: str, allow_login: bool = False):
            raise IngestionConfigurationError(
                "Telethon session could not be opened cleanly. Run scripts/ingest_once.py to refresh the local session."
            )

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(UserChannelService, "add_public_channel_for_user", new=fake_add_public_channel_for_user):
                await bot_start._handle_add_channel_submission(message, state, "@publicsource")

        self.assertFalse(state.cleared)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("scripts/ingest_once.py", message.answers[0]["text"])


class BotVoiceFlowTests(SessionTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_voice_message_is_transcribed_and_answered_from_context(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage(
            text=None,
            user_id=9201,
            username="voiceuser",
            full_name="Voice User",
            voice=DummyVoice(file_id="voice-123", file_size=2048),
        )
        fd, raw_path = tempfile.mkstemp(prefix="voice-handler-", suffix=".ogg")
        os.close(fd)
        audio_path = Path(raw_path)
        audio_path.write_bytes(b"fake audio")
        captured: dict[str, object] = {}

        async def fake_download_voice_message(message_arg, bot_arg) -> Path:
            captured["download_file_id"] = message_arg.voice.file_id
            return audio_path

        class FakeSTT:
            def transcribe(self, audio_path_arg: Path) -> str:
                captured["audio_path"] = audio_path_arg
                return "What did OpenAI publish?"

        class FakeQAService:
            def __init__(self, session_arg) -> None:
                captured["session"] = session_arg

            def answer(self, user_id: int, question: str, window_days: int):
                captured["qa_args"] = (user_id, question, window_days)
                return QAResponse(
                    question=question,
                    window_days=window_days,
                    answer_text="OpenAI published an MCP update [1].",
                    sources=[
                        QASource(
                            index=1,
                            channel_name="OpenAI Feed",
                            published_at=datetime.now(timezone.utc),
                            published_at_text="2026-04-22 10:00 UTC",
                            source_url="https://t.me/openaifeed/1",
                            source_label="OpenAI Feed / msg 1",
                            snippet="OpenAI published an MCP update.",
                        )
                    ],
                    used_fallback=False,
                    weak_evidence=False,
                )

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(bot_start, "_download_voice_message", new=fake_download_voice_message):
                with patch.object(bot_start, "STTService", new=lambda: FakeSTT()):
                    with patch.object(bot_start, "QAService", new=FakeQAService):
                        await bot_start.voice_message_handler(message, object())

        self.assertEqual(captured["download_file_id"], "voice-123")
        self.assertEqual(captured["audio_path"], audio_path)
        self.assertEqual(captured["qa_args"][1], "What did OpenAI publish?")
        self.assertEqual(captured["qa_args"][2], DEFAULT_DIGEST_WINDOW_DAYS)
        self.assertFalse(audio_path.exists())
        self.assertEqual(len(message.answers), 2)
        self.assertIn("Распознаю", message.answers[0]["text"])
        self.assertIn("What did OpenAI publish?", message.answers[1]["text"])
        self.assertIn("OpenAI published an MCP update [1].", message.answers[1]["text"])
        self.assertIn("https://t.me/openaifeed/1", message.answers[1]["text"])

    async def test_voice_message_returns_stt_error_without_crash(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage(
            text=None,
            user_id=9202,
            username="nostt",
            full_name="No STT",
            voice=DummyVoice(file_id="voice-err", file_size=1024),
        )
        fd, raw_path = tempfile.mkstemp(prefix="voice-handler-error-", suffix=".ogg")
        os.close(fd)
        audio_path = Path(raw_path)
        audio_path.write_bytes(b"fake audio")

        async def fake_download_voice_message(message_arg, bot_arg) -> Path:
            return audio_path

        class BrokenSTT:
            def transcribe(self, audio_path_arg: Path) -> str:
                raise STTConfigurationError("STT key is missing")

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(bot_start, "_download_voice_message", new=fake_download_voice_message):
                with patch.object(bot_start, "STTService", new=lambda: BrokenSTT()):
                    await bot_start.voice_message_handler(message, object())

        self.assertFalse(audio_path.exists())
        self.assertEqual(len(message.answers), 2)
        self.assertIn("Не удалось распознать", message.answers[1]["text"])
        self.assertIn("STT key is missing", message.answers[1]["text"])


    async def test_text_message_is_answered_by_assistant(self) -> None:
        session, _engine = self.make_session()
        message = DummyMessage(
            text="эти новости не про google",
            user_id=9203,
            username="textuser",
            full_name="Text User",
        )
        captured: dict[str, object] = {}

        class FakeQAService:
            def __init__(self, session_arg) -> None:
                captured["session"] = session_arg

            def answer(self, user_id: int, question: str, window_days: int):
                captured["qa_args"] = (user_id, question, window_days)
                return QAResponse(
                    question=question,
                    window_days=window_days,
                    answer_text="Недостаточно данных по google.",
                    sources=[],
                    used_fallback=True,
                    weak_evidence=True,
                )

        with patch.object(bot_start, "SessionLocal", new=lambda: session):
            with patch.object(bot_start, "QAService", new=FakeQAService):
                await bot_start.assistant_text_message_handler(message)

        self.assertEqual(captured["qa_args"][1], "эти новости не про google")
        self.assertEqual(captured["qa_args"][2], DEFAULT_DIGEST_WINDOW_DAYS)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("эти новости не про google", message.answers[0]["text"])
        self.assertIn("Недостаточно данных по google.", message.answers[0]["text"])

    async def test_reserved_text_messages_are_not_answered_by_assistant(self) -> None:
        session, _engine = self.make_session()

        class RaisingQAService:
            def __init__(self, session_arg) -> None:
                pass

            def answer(self, user_id: int, question: str, window_days: int):
                raise AssertionError("Reserved bot commands must not hit QAService")

        for text in ("Help", "Digest", "Add channel", "/digest"):
            with self.subTest(text=text):
                message = DummyMessage(text=text)
                with patch.object(bot_start, "SessionLocal", new=lambda: session):
                    with patch.object(bot_start, "QAService", new=RaisingQAService):
                        await bot_start.assistant_text_message_handler(message)
                self.assertEqual(message.answers, [])


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

    def test_ranking_uses_channel_relative_baseline(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=705,
            username="relativeuser",
            display_name="Relative User",
        )
        large_channel = self.create_user_added_channel(session, user.id, "bigfeed", "Big Feed", enabled=True)
        niche_channel = self.create_user_added_channel(session, user.id, "nichefeed", "Niche Feed", enabled=True)

        now = datetime.now(timezone.utc)
        for offset, views, reactions, forwards, comments in (
            (10, 1900, 220, 50, 20),
            (11, 2100, 240, 55, 22),
            (12, 2200, 250, 58, 24),
        ):
            session.add(
                Post(
                    channel_id=large_channel.id,
                    telegram_message_id=500 + offset,
                    raw_text=f"Large baseline {offset}",
                    cleaned_text=f"Large channel routine update {offset} about infrastructure rollout.",
                    source_url=f"https://t.me/bigfeed/{500 + offset}",
                    views_count=views,
                    reactions_count=reactions,
                    forwards_count=forwards,
                    comments_count=comments,
                    published_at=now - timedelta(hours=offset),
                )
            )

        large_candidate = Post(
            channel_id=large_channel.id,
            telegram_message_id=599,
            raw_text="Large channel candidate",
            cleaned_text="Large channel product note about a routine model refresh and rollout plan.",
            source_url="https://t.me/bigfeed/599",
            views_count=2300,
            reactions_count=260,
            forwards_count=60,
            comments_count=25,
            published_at=now - timedelta(hours=1),
        )
        niche_candidate = Post(
            channel_id=niche_channel.id,
            telegram_message_id=699,
            raw_text="Niche channel breakout",
            cleaned_text="Small channel exclusive: startup ships an open source agent runner with browser automation and local eval tooling.",
            source_url="https://t.me/nichefeed/699",
            views_count=420,
            reactions_count=48,
            forwards_count=12,
            comments_count=6,
            published_at=now - timedelta(hours=2),
        )
        for offset, views, reactions, forwards, comments in (
            (14, 70, 7, 1, 0),
            (15, 60, 5, 1, 0),
            (16, 50, 4, 0, 0),
        ):
            session.add(
                Post(
                    channel_id=niche_channel.id,
                    telegram_message_id=600 + offset,
                    raw_text=f"Niche baseline {offset}",
                    cleaned_text=f"Niche channel regular update {offset} about community links.",
                    source_url=f"https://t.me/nichefeed/{600 + offset}",
                    views_count=views,
                    reactions_count=reactions,
                    forwards_count=forwards,
                    comments_count=comments,
                    published_at=now - timedelta(hours=offset),
                )
            )

        session.add(large_candidate)
        session.add(niche_candidate)
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id, max_items=2)
        digest_items = list(session.scalars(select(DigestItem).order_by(DigestItem.id.asc())))

        self.assertTrue(result.has_content)
        self.assertEqual(len(digest_items), 2)
        self.assertEqual(digest_items[0].post_id, niche_candidate.id)

    def test_digest_deduplicates_same_news_across_channels(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=706,
            username="dedupeuser",
            display_name="Dedupe User",
        )
        first_channel = self.create_user_added_channel(session, user.id, "alphafeed", "Alpha Feed", enabled=True)
        second_channel = self.create_user_added_channel(session, user.id, "betafeed", "Beta Feed", enabled=True)
        third_channel = self.create_user_added_channel(session, user.id, "gammafeed", "Gamma Feed", enabled=True)

        now = datetime.now(timezone.utc)
        session.add(
            Post(
                channel_id=first_channel.id,
                telegram_message_id=801,
                raw_text="Rosalind release",
                cleaned_text=(
                    "OpenAI released GPT Rosalind for biology and chemistry research, aiming to speed up drug discovery workflows."
                ),
                source_url="https://t.me/alphafeed/801",
                views_count=900,
                reactions_count=90,
                forwards_count=20,
                comments_count=8,
                published_at=now - timedelta(hours=1),
            )
        )
        session.add(
            Post(
                channel_id=second_channel.id,
                telegram_message_id=802,
                raw_text="Rosalind copy",
                cleaned_text=(
                    "GPT Rosalind from OpenAI targets biology and chemistry research and helps drug discovery teams move faster."
                ),
                source_url="https://t.me/betafeed/802",
                views_count=850,
                reactions_count=88,
                forwards_count=19,
                comments_count=7,
                published_at=now - timedelta(hours=2),
            )
        )
        session.add(
            Post(
                channel_id=third_channel.id,
                telegram_message_id=803,
                raw_text="Distinct topic",
                cleaned_text=(
                    "Anthropic added a new API budget control mode so teams can cap token usage per task and per run."
                ),
                source_url="https://t.me/gammafeed/803",
                views_count=700,
                reactions_count=75,
                forwards_count=18,
                comments_count=5,
                published_at=now - timedelta(hours=3),
            )
        )
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id)

        self.assertTrue(result.has_content)
        self.assertEqual(result.digest.source_post_count, 2)
        duplicate_urls = {
            "https://t.me/alphafeed/801",
            "https://t.me/betafeed/802",
        }
        present_duplicate_urls = {url for url in duplicate_urls if url in result.message_text}
        self.assertEqual(len(present_duplicate_urls), 1)
        self.assertIn("https://t.me/gammafeed/803", result.message_text)

    def test_digest_selection_promotes_channel_diversity(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=707,
            username="diversityuser",
            display_name="Diversity User",
        )
        dominant_channel = self.create_user_added_channel(session, user.id, "dominantfeed", "Dominant Feed", enabled=True)
        secondary_channel = self.create_user_added_channel(session, user.id, "secondaryfeed", "Secondary Feed", enabled=True)

        now = datetime.now(timezone.utc)
        session.add_all(
            [
                Post(
                    channel_id=dominant_channel.id,
                    telegram_message_id=901,
                    raw_text="Dominant 1",
                    cleaned_text="Dominant channel ships a new coding agent benchmark and publishes the evaluation harness.",
                    source_url="https://t.me/dominantfeed/901",
                    views_count=1400,
                    reactions_count=140,
                    forwards_count=35,
                    comments_count=9,
                    published_at=now - timedelta(hours=1),
                ),
                Post(
                    channel_id=dominant_channel.id,
                    telegram_message_id=902,
                    raw_text="Dominant 2",
                    cleaned_text="Dominant channel also launches a separate observability release for production agent tracing.",
                    source_url="https://t.me/dominantfeed/902",
                    views_count=1300,
                    reactions_count=120,
                    forwards_count=30,
                    comments_count=8,
                    published_at=now - timedelta(hours=2),
                ),
                Post(
                    channel_id=secondary_channel.id,
                    telegram_message_id=903,
                    raw_text="Secondary",
                    cleaned_text="Secondary channel shares a practical guide for evaluating browser agents on real support workflows.",
                    source_url="https://t.me/secondaryfeed/903",
                    views_count=500,
                    reactions_count=45,
                    forwards_count=10,
                    comments_count=4,
                    published_at=now - timedelta(hours=3),
                ),
            ]
        )
        session.commit()

        result = DigestService(session, llm=DummyLLM()).generate_digest_for_user(user.id, max_items=2)
        digest_items = list(session.scalars(select(DigestItem).order_by(DigestItem.id.asc())))

        self.assertTrue(result.has_content)
        self.assertEqual(len(digest_items), 2)
        self.assertEqual({item.channel_title for item in digest_items}, {"Dominant Feed", "Secondary Feed"})

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


class AssistantServiceTests(SessionTestMixin, unittest.TestCase):
    def test_retrieval_respects_user_data_boundaries(self) -> None:
        session, _engine = self.make_session()
        first_user = UserService(session).upsert_telegram_user(
            telegram_user_id=801,
            username="firstassistant",
            display_name="First Assistant User",
        )
        second_user = UserService(session).upsert_telegram_user(
            telegram_user_id=802,
            username="secondassistant",
            display_name="Second Assistant User",
        )
        first_channel = self.create_user_added_channel(session, first_user.id, "mcpalpha", "MCP Alpha", enabled=True)
        second_channel = self.create_user_added_channel(session, second_user.id, "mcpbeta", "MCP Beta", enabled=True)

        now = datetime.now(timezone.utc)
        session.add_all(
            [
                Post(
                    channel_id=first_channel.id,
                    telegram_message_id=1001,
                    raw_text="MCP server update",
                    cleaned_text="OpenAI ecosystem got a fresh MCP server update for tool integrations.",
                    source_url="https://t.me/mcpalpha/1001",
                    published_at=now - timedelta(hours=2),
                ),
                Post(
                    channel_id=second_channel.id,
                    telegram_message_id=1002,
                    raw_text="Other MCP update",
                    cleaned_text="Another user's channel also wrote about MCP rollout details.",
                    source_url="https://t.me/mcpbeta/1002",
                    published_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.commit()

        response = QAService(session, llm=DummyLLM()).answer(
            user_id=first_user.id,
            question="Какие каналы писали про MCP?",
            window_days=7,
        )

        self.assertTrue(response.sources)
        self.assertEqual({source.channel_name for source in response.sources}, {"MCP Alpha"})
        self.assertNotIn("MCP Beta", response.answer_text)

    def test_no_results_are_graceful(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=803,
            username="nogrok",
            display_name="No Grok",
        )
        self.create_user_added_channel(session, user.id, "emptyassistant", "Empty Assistant", enabled=True)

        response = QAService(session, llm=DummyLLM()).answer(
            user_id=user.id,
            question="Что было по теме Grok за неделю?",
            window_days=7,
        )

        self.assertTrue(response.used_fallback)
        self.assertTrue(response.weak_evidence)
        self.assertEqual(response.sources, [])
        self.assertIn("Недостаточно данных", response.answer_text)

    def test_fallback_answer_contains_citations_without_external_llm(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=804,
            username="fallbackassistant",
            display_name="Fallback Assistant",
        )
        channel = self.create_user_added_channel(session, user.id, "openaifeed", "OpenAI Feed", enabled=True)

        session.add(
            Post(
                channel_id=channel.id,
                telegram_message_id=1101,
                raw_text="OpenAI ships MCP update",
                cleaned_text="OpenAI shipped a new MCP integration update and refreshed developer docs for tool calling.",
                source_url="https://t.me/openaifeed/1101",
                published_at=datetime.now(timezone.utc) - timedelta(hours=3),
            )
        )
        session.commit()

        response = QAService(session, llm=DummyLLM()).answer(
            user_id=user.id,
            question="Что нового по OpenAI за 3 дня?",
            window_days=3,
        )

        self.assertTrue(response.used_fallback)
        self.assertFalse(response.weak_evidence)
        self.assertGreaterEqual(len(response.sources), 1)
        self.assertIn("[1]", response.answer_text)
        self.assertIn("OpenAI Feed", response.answer_text)

    def test_anchor_query_does_not_return_unrelated_brand_posts(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=805,
            username="googlefilter",
            display_name="Google Filter",
        )
        channel = self.create_user_added_channel(session, user.id, "aifeed", "AI Feed", enabled=True)

        now = datetime.now(timezone.utc)
        session.add_all(
            [
                Post(
                    channel_id=channel.id,
                    telegram_message_id=1301,
                    raw_text="Kimi ships a new coding model",
                    cleaned_text="Kimi released a new code preview model for SWE-Bench tasks.",
                    source_url="https://t.me/aifeed/1301",
                    published_at=now - timedelta(hours=2),
                ),
                Post(
                    channel_id=channel.id,
                    telegram_message_id=1302,
                    raw_text="Claude pricing update",
                    cleaned_text="Anthropic updated Claude pricing and subscription tiers.",
                    source_url="https://t.me/aifeed/1302",
                    published_at=now - timedelta(hours=1),
                ),
                Post(
                    channel_id=channel.id,
                    telegram_message_id=1303,
                    raw_text="Yandex practical AI release",
                    cleaned_text="Yandex launched a practical AI release for developers.",
                    source_url="https://t.me/aifeed/1303",
                    published_at=now - timedelta(hours=3),
                ),
            ]
        )
        session.commit()

        response = QAService(session, llm=DummyLLM()).answer(
            user_id=user.id,
            question="Расскажи про новые продукты Google за неделю",
            window_days=7,
        )

        self.assertTrue(response.used_fallback)
        self.assertTrue(response.weak_evidence)
        self.assertEqual(response.sources, [])
        self.assertNotIn("Kimi", response.answer_text)
        self.assertNotIn("Claude", response.answer_text)
        self.assertNotIn("Yandex", response.answer_text)

    def test_anchor_query_returns_google_text_and_digest_hint_sources(self) -> None:
        session, _engine = self.make_session()
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=806,
            username="googlehint",
            display_name="Google Hint",
        )
        channel = self.create_user_added_channel(session, user.id, "productfeed", "Product Feed", enabled=True)

        now = datetime.now(timezone.utc)
        google_text_post = Post(
            channel_id=channel.id,
            telegram_message_id=1401,
            raw_text="Google product launch",
            cleaned_text="Google showed a new developer product for agents and file validation this week.",
            source_url="https://t.me/productfeed/1401",
            published_at=now - timedelta(hours=2),
        )
        digest_hint_post = Post(
            channel_id=channel.id,
            telegram_message_id=1402,
            raw_text="Developer tooling update",
            cleaned_text="A developer tooling update shipped with design document validation.",
            source_url="https://t.me/productfeed/1402",
            published_at=now - timedelta(hours=1),
        )
        unrelated_post = Post(
            channel_id=channel.id,
            telegram_message_id=1403,
            raw_text="Kimi product launch",
            cleaned_text="Kimi released a new code model for SWE-Bench tasks.",
            source_url="https://t.me/productfeed/1403",
            published_at=now - timedelta(minutes=30),
        )
        session.add_all([google_text_post, digest_hint_post, unrelated_post])
        session.flush()
        digest = Digest(
            user_id=user.id,
            status="ready",
            delivery_status="sent",
            body_text="Digest with Google tooling note",
            source_post_count=2,
        )
        session.add(digest)
        session.flush()
        session.add(
            DigestItem(
                digest_id=digest.id,
                post_id=digest_hint_post.id,
                channel_title=channel.title,
                title="Google developer tooling",
                summary="Google showed a product update for agent workflows.",
                source_url=digest_hint_post.source_url,
                score=1.0,
                published_at=digest_hint_post.published_at,
            )
        )
        session.commit()

        response = QAService(session, llm=DummyLLM()).answer(
            user_id=user.id,
            question="Расскажи подробнее про продукты Google",
            window_days=7,
        )
        source_urls = {source.source_url for source in response.sources}

        self.assertIn("https://t.me/productfeed/1401", source_urls)
        self.assertIn("https://t.me/productfeed/1402", source_urls)
        self.assertNotIn("https://t.me/productfeed/1403", source_urls)


class WebSurfaceTests(SessionTestMixin, unittest.TestCase):
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

        for path in ("/app", "/app/assistant", "/app/digests", "/app/subscriptions"):
            with self.subTest(path=path):
                response = client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers["location"].startswith("/login?next="))

    def test_assistant_post_redirects_without_session(self) -> None:
        client = self.make_web_client()

        response = client.post(
            "/app/assistant",
            data={"question": "Что нового по OpenAI?", "window_days": "7"},
            follow_redirects=False,
        )

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

    def test_assistant_page_loads_and_ask_returns_citations(self) -> None:
        client = self.make_web_client()
        user_id, _code_id, link_code = self.create_user_with_code(
            telegram_user_id=408,
            username="assistantviewer",
            display_name="Assistant Viewer",
        )

        with SessionLocal() as session:
            channel = self.create_user_added_channel(session, user_id, "assistantfeed", "Assistant Feed", enabled=True)
            session.add(
                Post(
                    channel_id=channel.id,
                    telegram_message_id=1201,
                    raw_text="OpenAI follow-up",
                    cleaned_text="OpenAI published a new MCP support update for developer tooling and integrations.",
                    source_url="https://t.me/assistantfeed/1201",
                    published_at=datetime.now(timezone.utc) - timedelta(hours=4),
                )
            )
            session.commit()

        self.redeem_code(client, link_code)

        page_response = client.get("/app/assistant")
        self.assertEqual(page_response.status_code, 200)
        self.assertIn("Ask assistant", page_response.text)

        ask_response = client.post(
            "/app/assistant",
            data={"question": "Что нового по OpenAI за 3 дня?", "window_days": "3"},
            follow_redirects=True,
        )

        self.assertEqual(ask_response.status_code, 200)
        self.assertIn("[1]", ask_response.text)
        self.assertIn("Источники", ask_response.text)
        self.assertIn("https://t.me/assistantfeed/1201", ask_response.text)


if __name__ == "__main__":
    unittest.main()
