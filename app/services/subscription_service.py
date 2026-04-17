from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Subscription, User


class SubscriptionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_subscriptions(self, user_id: int) -> list[Subscription]:
        statement = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.channel_id.asc())
        )
        return list(self.session.scalars(statement))

    def list_subscribed_channels(self, user_id: int) -> list[Channel]:
        statement = (
            select(Channel)
            .join(Subscription, Subscription.channel_id == Channel.id)
            .where(Subscription.user_id == user_id, Subscription.enabled.is_(True))
            .order_by(Channel.title.asc())
        )
        return list(self.session.scalars(statement))

    def get_subscription_map(self, user_id: int) -> dict[int, Subscription]:
        return {item.channel_id: item for item in self.list_subscriptions(user_id)}

    def set_subscription(
        self,
        user_id: int,
        channel_id: int,
        enabled: bool,
        frequency: str = "daily",
    ) -> Subscription:
        self._require_user(user_id)
        self._require_channel(channel_id)

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
                enabled=enabled,
                frequency=frequency,
            )
            self.session.add(subscription)
        else:
            subscription.enabled = enabled
            subscription.frequency = frequency

        self.session.commit()
        self.session.refresh(subscription)
        return subscription

    def toggle_subscription(self, user_id: int, channel_id: int) -> Subscription:
        subscription = self.session.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.channel_id == channel_id,
            )
        )
        next_enabled = True if subscription is None else not subscription.enabled
        return self.set_subscription(user_id, channel_id, enabled=next_enabled)

    def _require_user(self, user_id: int) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise ValueError(f"Unknown user_id: {user_id}")
        return user

    def _require_channel(self, channel_id: int) -> Channel:
        channel = self.session.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Unknown channel_id: {channel_id}")
        return channel
