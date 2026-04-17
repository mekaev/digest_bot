import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Topic

SEED_PATH = Path(__file__).resolve().parent.parent / "catalog_seed.json"
USER_ADDED_TOPIC_SLUG = "user-added"


class CatalogService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def seed_catalog(self) -> None:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))

        topics_by_slug: dict[str, Topic] = {}
        for topic_payload in payload.get("topics", []):
            topic = self.session.scalar(
                select(Topic).where(Topic.slug == topic_payload["slug"])
            )
            if topic is None:
                topic = Topic(
                    slug=topic_payload["slug"],
                    name=topic_payload["name"],
                    description=topic_payload.get("description", ""),
                )
                self.session.add(topic)
                self.session.flush()
            else:
                topic.name = topic_payload["name"]
                topic.description = topic_payload.get("description", "")
            topics_by_slug[topic.slug] = topic

        for channel_payload in payload.get("channels", []):
            topic = topics_by_slug[channel_payload["topic_slug"]]
            channel = self.session.scalar(
                select(Channel).where(Channel.telegram_handle == channel_payload["telegram_handle"])
            )
            if channel is None:
                channel = Channel(
                    topic_id=topic.id,
                    telegram_handle=channel_payload["telegram_handle"],
                    title=channel_payload["title"],
                    description=channel_payload.get("description", ""),
                    is_active=True,
                    is_user_added=False,
                    added_by_user_id=None,
                )
                self.session.add(channel)
            else:
                channel.topic_id = topic.id
                channel.title = channel_payload["title"]
                channel.description = channel_payload.get("description", "")
                channel.is_active = True
                channel.is_user_added = False
                channel.added_by_user_id = None

        self.session.commit()

    def list_topics(self) -> list[Topic]:
        statement = (
            select(Topic)
            .where(Topic.slug != USER_ADDED_TOPIC_SLUG)
            .order_by(Topic.name.asc())
        )
        return list(self.session.scalars(statement))

    def list_channels(self, topic_id: int | None = None) -> list[Channel]:
        statement = select(Channel).where(
            Channel.is_active.is_(True),
            Channel.is_user_added.is_(False),
        )
        if topic_id is not None:
            statement = statement.where(Channel.topic_id == topic_id)
        statement = statement.order_by(Channel.title.asc())
        return list(self.session.scalars(statement))

    def get_topic(self, topic_id: int) -> Topic | None:
        return self.session.get(Topic, topic_id)

    def get_channel(self, channel_id: int) -> Channel | None:
        return self.session.get(Channel, channel_id)
