from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError
from telethon.tl import types

from app.config import BASE_DIR, get_settings


class IngestionConfigurationError(RuntimeError):
    pass


class ChannelValidationError(ValueError):
    pass


@dataclass(slots=True)
class TelegramChannel:
    telegram_handle: str
    title: str
    description: str


@dataclass(slots=True)
class TelegramMessage:
    telegram_message_id: int
    raw_text: str
    cleaned_text: str
    channel_handle: str
    published_at: datetime
    source_url: str

    @property
    def message_id(self) -> int:
        return self.telegram_message_id

    @property
    def text(self) -> str:
        return self.cleaned_text


class TelegramIngestionClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_id = int(settings.telegram_api_id) if settings.telegram_api_id.strip() else 0
        self.api_hash = settings.telegram_api_hash.strip()
        self.phone = settings.telegram_phone.strip()
        self.session_path = BASE_DIR / "data" / "telethon_ingestion"

    def is_configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def normalize_message(self, payload: dict[str, Any], channel: str) -> TelegramMessage:
        raw_text = str(payload.get("text") or payload.get("message") or "").strip()
        published_at = payload.get("posted_at") or datetime.now(timezone.utc)
        message_id = int(payload.get("id", 0))
        return TelegramMessage(
            telegram_message_id=message_id,
            raw_text=raw_text,
            cleaned_text=_clean_text(raw_text),
            channel_handle=channel.lstrip("@"),
            published_at=published_at,
            source_url=self._build_source_url(channel, message_id),
        )

    async def validate_public_channel(
        self,
        channel_reference: str,
        allow_login: bool = False,
    ) -> TelegramChannel:
        normalized_handle = normalize_channel_reference(channel_reference)
        if not normalized_handle:
            raise ChannelValidationError(
                "Send a public channel as @username or https://t.me/username."
            )

        if not self.is_configured():
            raise IngestionConfigurationError(
                "Telethon ingestion is not configured. Set TELEGRAM_API_ID and TELEGRAM_API_HASH."
            )

        async with TelegramClient(str(self.session_path), self.api_id, self.api_hash) as client:
            if allow_login:
                if not self.phone:
                    raise IngestionConfigurationError(
                        "TELEGRAM_PHONE is required for the first Telethon authorization."
                    )
                await client.start(phone=self.phone)
            else:
                await client.connect()
                if not await client.is_user_authorized():
                    raise IngestionConfigurationError(
                        "Telethon session is not authorized. Run scripts/ingest_once.py first."
                    )

            try:
                entity = await client.get_entity(normalized_handle)
            except UsernameInvalidError as exc:
                raise ChannelValidationError(
                    "Send a valid public channel as @username or https://t.me/username."
                ) from exc
            except UsernameNotOccupiedError as exc:
                raise ChannelValidationError(
                    f"Channel @{normalized_handle} was not found or is unavailable."
                ) from exc
            except ChannelPrivateError as exc:
                raise ChannelValidationError(
                    "Private channels are not supported. Add a public channel instead."
                ) from exc
            except ValueError as exc:
                raise ChannelValidationError(
                    f"Channel @{normalized_handle} was not found or is unavailable."
                ) from exc

        if not isinstance(entity, types.Channel):
            raise ChannelValidationError("Only public Telegram channels are supported.")

        public_handle = (getattr(entity, "username", "") or "").strip().lower()
        if not public_handle:
            raise ChannelValidationError(
                "Only public channels with a visible @username are supported."
            )

        title = (getattr(entity, "title", "") or public_handle).strip() or public_handle
        return TelegramChannel(
            telegram_handle=public_handle,
            title=title,
            description="",
        )

    async def fetch_messages(
        self,
        channel_handle: str,
        limit: int = 20,
        allow_login: bool = False,
    ) -> list[TelegramMessage]:
        if not self.is_configured():
            raise IngestionConfigurationError(
                "Telethon ingestion is not configured. Set TELEGRAM_API_ID and TELEGRAM_API_HASH."
            )

        normalized_handle = channel_handle.lstrip("@")
        async with TelegramClient(str(self.session_path), self.api_id, self.api_hash) as client:
            if allow_login:
                if not self.phone:
                    raise IngestionConfigurationError(
                        "TELEGRAM_PHONE is required for the first Telethon authorization."
                    )
                await client.start(phone=self.phone)
            else:
                await client.connect()
                if not await client.is_user_authorized():
                    raise IngestionConfigurationError(
                        "Telethon session is not authorized. Run scripts/ingest_once.py first."
                    )

            entity = await client.get_entity(normalized_handle)
            messages: list[TelegramMessage] = []
            async for message in client.iter_messages(entity, limit=limit):
                raw_text = getattr(message, "message", "") or ""
                published_at = getattr(message, "date", None) or datetime.now(timezone.utc)
                message_id = int(getattr(message, "id", 0))
                messages.append(
                    TelegramMessage(
                        telegram_message_id=message_id,
                        raw_text=raw_text,
                        cleaned_text=_clean_text(raw_text),
                        channel_handle=normalized_handle,
                        published_at=published_at,
                        source_url=self._build_source_url(normalized_handle, message_id),
                    )
                )
            return messages

    def _build_source_url(self, channel_handle: str, message_id: int) -> str:
        return f"https://t.me/{channel_handle.lstrip('@')}/{message_id}"


def _clean_text(value: str) -> str:
    return " ".join(value.split()).strip()


def normalize_channel_reference(value: str) -> str:
    raw_value = value.strip()
    if not raw_value:
        return ""

    if raw_value.startswith("@"):
        handle = raw_value[1:].strip().lower()
        return handle if _is_valid_public_handle(handle) else ""

    parsed = urlparse(raw_value)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if parsed.netloc.lower() not in {"t.me", "www.t.me"}:
        return ""

    path = parsed.path.strip("/")
    if not path or "/" in path:
        return ""
    handle = path.lower()
    return handle if _is_valid_public_handle(handle) else ""


def _is_valid_public_handle(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{3,31}", value))
