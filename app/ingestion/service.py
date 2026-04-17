from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, IngestionRun, Post, Subscription
from app.ingestion.telegram_client import (
    IngestionConfigurationError,
    TelegramIngestionClient,
    TelegramMessage,
)


class IngestionService:
    def __init__(
        self,
        session: Session,
        client: TelegramIngestionClient | None = None,
    ) -> None:
        self.session = session
        self.client = client or TelegramIngestionClient()

    async def ingest_user_subscriptions(
        self,
        user_id: int,
        limit: int = 20,
        allow_login: bool = False,
        user_added_only: bool = False,
    ) -> list[IngestionRun]:
        statement = (
            select(Channel)
            .join(Subscription, Subscription.channel_id == Channel.id)
            .where(Subscription.user_id == user_id, Subscription.enabled.is_(True))
            .order_by(Channel.title.asc())
        )
        if user_added_only:
            statement = statement.where(Channel.is_user_added.is_(True))
        channels = list(self.session.scalars(statement))
        return await self.ingest_channels(channels, limit=limit, allow_login=allow_login)

    async def ingest_channels(
        self,
        channels: list[Channel],
        limit: int = 20,
        allow_login: bool = False,
    ) -> list[IngestionRun]:
        runs: list[IngestionRun] = []
        for channel in channels:
            run = IngestionRun(
                channel_id=channel.id,
                status="running",
                fetched_count=0,
                stored_count=0,
                error_message="",
                started_at=datetime.now(timezone.utc),
            )
            self.session.add(run)
            self.session.flush()

            try:
                messages = await self.client.fetch_messages(
                    channel.telegram_handle,
                    limit=limit,
                    allow_login=allow_login,
                )
                run.fetched_count = len(messages)
                run.stored_count = self.store_messages(channel, messages)
                run.status = "success"
            except IngestionConfigurationError as exc:
                run.status = "failed"
                run.error_message = str(exc)
            except Exception as exc:  # noqa: BLE001
                run.status = "failed"
                run.error_message = str(exc)
            finally:
                run.finished_at = datetime.now(timezone.utc)
                self.session.commit()
                self.session.refresh(run)
                runs.append(run)
        return runs

    def store_messages(self, channel: Channel, messages: list[TelegramMessage]) -> int:
        stored_count = 0
        known_message_ids = set(
            self.session.scalars(
                select(Post.telegram_message_id).where(Post.channel_id == channel.id)
            )
        )
        for message in messages:
            cleaned_text = message.cleaned_text.strip()
            if not cleaned_text:
                continue

            if message.telegram_message_id in known_message_ids:
                continue

            post = Post(
                channel_id=channel.id,
                telegram_message_id=message.telegram_message_id,
                raw_text=message.raw_text,
                cleaned_text=cleaned_text,
                source_url=message.source_url,
                views_count=message.views_count,
                reactions_count=message.reactions_count,
                forwards_count=message.forwards_count,
                comments_count=message.comments_count,
                published_at=message.published_at,
            )
            self.session.add(post)
            known_message_ids.add(message.telegram_message_id)
            stored_count += 1

        self.session.commit()
        return stored_count
