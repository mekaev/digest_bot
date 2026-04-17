from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient

from app.config import BASE_DIR, get_settings


class IngestionConfigurationError(RuntimeError):
    pass


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
