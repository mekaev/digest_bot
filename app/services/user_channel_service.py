from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Subscription, Topic, User
from app.ingestion.telegram_client import TelegramChannel, TelegramIngestionClient
from app.services.catalog_service import USER_ADDED_TOPIC_SLUG

USER_ADDED_TOPIC_NAME = "User Added"
USER_ADDED_TOPIC_DESCRIPTION = "Hidden bucket for user-added public channels."


@dataclass(slots=True)
class AddChannelResult:
    channel: Channel
    subscription: Subscription
    channel_created: bool
    subscription_created: bool
    already_enabled: bool


@dataclass(slots=True)
class UserChannelEntry:
    channel: Channel
    subscription: Subscription


class UserChannelService:
    def __init__(
        self,
        session: Session,
        client: TelegramIngestionClient | None = None,
    ) -> None:
        self.session = session
        self.client = client or TelegramIngestionClient()

    async def add_public_channel_for_user(
        self,
        user_id: int,
        channel_reference: str,
        allow_login: bool = False,
    ) -> AddChannelResult:
        user = self._require_user(user_id)
        validated_channel = await self.client.validate_public_channel(
            channel_reference,
            allow_login=allow_login,
        )

        channel = self.session.scalar(
            select(Channel).where(Channel.telegram_handle == validated_channel.telegram_handle)
        )
        channel_created = False
        if channel is None:
            topic = self._get_or_create_user_added_topic()
            channel = Channel(
                topic_id=topic.id,
                telegram_handle=validated_channel.telegram_handle,
                title=validated_channel.title,
                description=validated_channel.description,
                is_active=True,
                is_user_added=True,
                added_by_user_id=user.id,
            )
            self.session.add(channel)
            self.session.flush()
            channel_created = True
        else:
            self._refresh_existing_channel(channel, validated_channel)

        subscription = self.session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.channel_id == channel.id,
            )
        )
        subscription_created = False
        already_enabled = False
        if subscription is None:
            subscription = Subscription(
                user_id=user.id,
                channel_id=channel.id,
                enabled=True,
                frequency="daily",
            )
            self.session.add(subscription)
            subscription_created = True
        else:
            already_enabled = subscription.enabled
            subscription.enabled = True

        self.session.commit()
        self.session.refresh(channel)
        self.session.refresh(subscription)
        return AddChannelResult(
            channel=channel,
            subscription=subscription,
            channel_created=channel_created,
            subscription_created=subscription_created,
            already_enabled=already_enabled,
        )

    def list_user_added_channels(self, user_id: int) -> list[UserChannelEntry]:
        self._require_user(user_id)
        rows = self.session.execute(
            select(Channel, Subscription)
            .join(Subscription, Subscription.channel_id == Channel.id)
            .where(
                Subscription.user_id == user_id,
                Channel.is_user_added.is_(True),
            )
            .order_by(Channel.title.asc(), Channel.telegram_handle.asc())
        ).all()
        return [
            UserChannelEntry(channel=channel, subscription=subscription)
            for channel, subscription in rows
        ]

    def toggle_user_channel(self, user_id: int, channel_id: int) -> Subscription:
        self._require_user(user_id)
        channel = self.session.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Unknown channel_id: {channel_id}")
        if not channel.is_user_added:
            raise ValueError("Only user-added channels can be managed in this flow.")

        subscription = self.session.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.channel_id == channel_id,
            )
        )
        if subscription is None:
            subscription = Subscription(
                user_id=user_id,
                channel_id=channel_id,
                enabled=True,
                frequency="daily",
            )
            self.session.add(subscription)
        else:
            subscription.enabled = not subscription.enabled

        self.session.commit()
        self.session.refresh(subscription)
        return subscription

    def remove_user_added_channel_for_user(self, user_id: int, channel_id: int) -> None:
        self._require_user(user_id)
        channel = self.session.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Unknown channel_id: {channel_id}")
        if not channel.is_user_added:
            raise ValueError("Only user-added channels can be removed from this section.")

        subscription = self.session.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.channel_id == channel_id,
            )
        )
        if subscription is None:
            raise ValueError("This user-added channel is not in your sources.")

        self.session.delete(subscription)
        self.session.commit()

    def _require_user(self, user_id: int) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise ValueError(f"Unknown user_id: {user_id}")
        return user

    def _get_or_create_user_added_topic(self) -> Topic:
        topic = self.session.scalar(
            select(Topic).where(Topic.slug == USER_ADDED_TOPIC_SLUG)
        )
        if topic is None:
            topic = Topic(
                slug=USER_ADDED_TOPIC_SLUG,
                name=USER_ADDED_TOPIC_NAME,
                description=USER_ADDED_TOPIC_DESCRIPTION,
            )
            self.session.add(topic)
            self.session.flush()
        return topic

    def _refresh_existing_channel(
        self,
        channel: Channel,
        validated_channel: TelegramChannel,
    ) -> None:
        channel.is_active = True
        if channel.is_user_added:
            channel.title = validated_channel.title
            channel.description = validated_channel.description
