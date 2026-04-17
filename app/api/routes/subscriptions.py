from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.services.subscription_service import SubscriptionService

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


class SubscriptionUpdateRequest(BaseModel):
    channel_id: int
    enabled: bool
    frequency: str = "daily"


class SubscriptionResponse(BaseModel):
    channel_id: int
    enabled: bool
    frequency: str


@router.get("/{user_id}", response_model=list[SubscriptionResponse])
async def read_subscriptions(user_id: int) -> list[SubscriptionResponse]:
    with SessionLocal() as session:
        service = SubscriptionService(session)
        subscriptions = service.list_subscriptions(user_id)

    return [
        SubscriptionResponse(
            channel_id=subscription.channel_id,
            enabled=subscription.enabled,
            frequency=subscription.frequency,
        )
        for subscription in subscriptions
    ]


@router.put("/{user_id}", response_model=SubscriptionResponse)
async def save_subscription(user_id: int, payload: SubscriptionUpdateRequest) -> SubscriptionResponse:
    with SessionLocal() as session:
        service = SubscriptionService(session)
        try:
            subscription = service.set_subscription(
                user_id=user_id,
                channel_id=payload.channel_id,
                enabled=payload.enabled,
                frequency=payload.frequency,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return SubscriptionResponse(
        channel_id=subscription.channel_id,
        enabled=subscription.enabled,
        frequency=subscription.frequency,
    )
