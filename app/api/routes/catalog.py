from pydantic import BaseModel
from fastapi import APIRouter

from app.db.session import SessionLocal
from app.services.catalog_service import CatalogService

router = APIRouter(prefix="/catalog", tags=["catalog"])


class TopicResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: str


class ChannelResponse(BaseModel):
    id: int
    topic_id: int
    telegram_handle: str
    title: str
    description: str


class CatalogResponse(BaseModel):
    topics: list[TopicResponse]
    channels: list[ChannelResponse]


@router.get("", response_model=CatalogResponse)
async def read_catalog() -> CatalogResponse:
    with SessionLocal() as session:
        service = CatalogService(session)
        topics = service.list_topics()
        channels = service.list_channels()

    return CatalogResponse(
        topics=[
            TopicResponse(
                id=topic.id,
                slug=topic.slug,
                name=topic.name,
                description=topic.description,
            )
            for topic in topics
        ],
        channels=[
            ChannelResponse(
                id=channel.id,
                topic_id=channel.topic_id,
                telegram_handle=channel.telegram_handle,
                title=channel.title,
                description=channel.description,
            )
            for channel in channels
        ],
    )
