from typing import Iterable

from app.ingestion.telegram_client import TelegramIngestionClient, TelegramMessage


class ChannelImporter:
    def __init__(self, client: TelegramIngestionClient | None = None) -> None:
        self.client = client or TelegramIngestionClient()

    def import_batch(self, channel: str, raw_messages: Iterable[dict]) -> list[TelegramMessage]:
        normalized_messages: list[TelegramMessage] = []
        for raw in raw_messages:
            message = self.client.normalize_message(raw, channel)
            if message.text:
                normalized_messages.append(message)
        return normalized_messages
