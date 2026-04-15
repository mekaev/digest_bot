from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class TelegramMessage:
    message_id: int
    text: str
    channel: str
    posted_at: datetime


class TelegramIngestionClient:
    def normalize_message(self, payload: dict[str, Any], channel: str) -> TelegramMessage:
        text = str(payload.get('text') or payload.get('message') or '').strip()
        return TelegramMessage(
            message_id=int(payload.get('id', 0)),
            text=text,
            channel=channel,
            posted_at=datetime.now(timezone.utc),
        )
