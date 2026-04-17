from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.db.models import User
from app.db.session import SessionLocal
from app.services.catalog_service import CatalogService
from app.services.digest_service import DigestService
from app.services.subscription_service import SubscriptionService
from app.services.user_service import UserService


router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    with SessionLocal() as session:
        current_user = _get_current_user(request, session)

    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "current_user": current_user,
            "page_title": "AI Telegram Digest Bot",
        },
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    next_path = _safe_next_path(str(request.query_params.get("next", "")))
    code_value = str(request.query_params.get("code", "")).strip().upper()

    with SessionLocal() as session:
        current_user = _get_current_user(request, session)
        if current_user is not None:
            return RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)

    return _render_login_page(request, code=code_value, next_path=next_path)


@router.post("/auth/redeem", response_class=HTMLResponse)
async def redeem_link_code(request: Request) -> Response:
    form_data = await _read_form_data(request)
    code = form_data.get("code", "").strip().upper()
    next_path = _safe_next_path(form_data.get("next", ""))

    with SessionLocal() as session:
        service = UserService(session)
        try:
            user = service.redeem_link_code(code)
        except ValueError as exc:
            return _render_login_page(
                request,
                code=code,
                error_message=str(exc),
                next_path=next_path,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/app", response_class=HTMLResponse)
async def app_profile_page(request: Request) -> Response:
    with SessionLocal() as session:
        current_user = _get_current_user(request, session)
        if current_user is None:
            return _redirect_to_login(request)

        subscribed_channels = SubscriptionService(session).list_subscribed_channels(current_user.id)
        recent_digests = DigestService(session).list_digests_for_user(current_user.id, limit=5)

    return templates.TemplateResponse(
        request=request,
        name="app_profile.html",
        context={
            "current_user": current_user,
            "subscribed_channels": subscribed_channels,
            "recent_digests": recent_digests,
            "page_title": "Profile",
        },
    )


@router.get("/app/digests", response_class=HTMLResponse)
async def app_digests_page(request: Request) -> Response:
    with SessionLocal() as session:
        current_user = _get_current_user(request, session)
        if current_user is None:
            return _redirect_to_login(request)

        digests = DigestService(session).list_digests_for_user(current_user.id)

    return templates.TemplateResponse(
        request=request,
        name="app_digests.html",
        context={
            "current_user": current_user,
            "digests": digests,
            "page_title": "Digests",
        },
    )


@router.get("/app/subscriptions", response_class=HTMLResponse)
async def app_subscriptions_page(request: Request) -> Response:
    with SessionLocal() as session:
        current_user = _get_current_user(request, session)
        if current_user is None:
            return _redirect_to_login(request)

        topic_sections = _build_subscription_sections(session, current_user.id)

    return templates.TemplateResponse(
        request=request,
        name="app_subscriptions.html",
        context={
            "current_user": current_user,
            "topic_sections": topic_sections,
            "page_title": "Subscriptions",
        },
    )


@router.post("/app/subscriptions/{channel_id}")
async def update_subscription(request: Request, channel_id: int) -> RedirectResponse:
    with SessionLocal() as session:
        current_user = _get_current_user(request, session)
        if current_user is None:
            return _redirect_to_login(request)

        form_data = await _read_form_data(request)
        enabled_raw = form_data.get("enabled")
        service = SubscriptionService(session)
        try:
            if enabled_raw is None:
                service.toggle_subscription(current_user.id, channel_id)
            else:
                service.set_subscription(
                    current_user.id,
                    channel_id,
                    enabled=_parse_enabled_flag(str(enabled_raw)),
                )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RedirectResponse(url="/app/subscriptions", status_code=status.HTTP_303_SEE_OTHER)


def _build_subscription_sections(session, user_id: int) -> list[dict]:
    catalog_service = CatalogService(session)
    subscription_map = SubscriptionService(session).get_subscription_map(user_id)

    topics = catalog_service.list_topics()
    channels = catalog_service.list_channels()
    channels_by_topic: dict[int, list] = defaultdict(list)
    for channel in channels:
        channels_by_topic[channel.topic_id].append(channel)

    sections = []
    for topic in topics:
        topic_channels = []
        for channel in channels_by_topic.get(topic.id, []):
            subscription = subscription_map.get(channel.id)
            topic_channels.append(
                {
                    "channel": channel,
                    "enabled": bool(subscription and subscription.enabled),
                }
            )
        sections.append({"topic": topic, "channels": topic_channels})
    return sections


def _get_current_user(request: Request, session) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None

    try:
        normalized_user_id = int(user_id)
    except (TypeError, ValueError):
        request.session.clear()
        return None

    user = UserService(session).get_by_id(normalized_user_id)
    if user is None:
        request.session.clear()
        return None
    return user


def _redirect_to_login(request: Request) -> RedirectResponse:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return RedirectResponse(
        url=f"/login?{urlencode({'next': next_path})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _render_login_page(
    request: Request,
    code: str = "",
    error_message: str = "",
    next_path: str = "/app",
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "current_user": None,
            "error_message": error_message,
            "code": code,
            "next_path": next_path,
            "page_title": "Login",
        },
        status_code=status_code,
    )


def _safe_next_path(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/app"


def _parse_enabled_flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def _read_form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}
