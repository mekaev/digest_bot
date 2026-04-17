from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.db.session import SessionLocal
from app.ingestion.service import IngestionService
from app.services.catalog_service import CatalogService
from app.services.digest_service import DigestService
from app.services.subscription_service import SubscriptionService
from app.services.user_service import UserService

router = Router()

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Help"),
            KeyboardButton(text="Link account"),
        ],
        [
            KeyboardButton(text="Channels"),
            KeyboardButton(text="Topics"),
        ],
        [KeyboardButton(text="Digest")],
    ],
    resize_keyboard=True,
)


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    if message.from_user is None:
        return

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username or "",
            display_name=message.from_user.full_name,
        )
        subscriptions = SubscriptionService(session).list_subscribed_channels(user.id)

    next_step = "/channels" if not subscriptions else "/digest"
    await message.answer(
        "AI Telegram Digest Bot is ready.\n"
        f"Your account is saved. Next step: use {next_step}.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Commands:\n"
        "/start - create or refresh your Telegram profile\n"
        "/link - generate a short-lived web link code\n"
        "/topics - browse curated topics\n"
        "/channels - toggle curated channel subscriptions\n"
        "/digest - fetch posts and send the latest digest",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("link"))
async def link_handler(message: Message) -> None:
    if message.from_user is None:
        return

    with SessionLocal() as session:
        service = UserService(session)
        user = service.upsert_telegram_user(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username or "",
            display_name=message.from_user.full_name,
        )
        link_code = service.get_or_create_link_code(user.id)

    expires_at = link_code.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    await message.answer(
        "Your link code is ready.\n"
        f"Code: {link_code.code}\n"
        f"Expires: {expires_at}",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("topics"))
async def topics_handler(message: Message) -> None:
    with SessionLocal() as session:
        topics = CatalogService(session).list_topics()

    await message.answer(
        "Curated topics:",
        reply_markup=_build_topics_keyboard(topics),
    )


@router.message(Command("channels"))
async def channels_handler(message: Message) -> None:
    if message.from_user is None:
        return

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username or "",
            display_name=message.from_user.full_name,
        )
        text, markup = _build_channels_view(session, user.id, topic_id=None)

    await message.answer(text, reply_markup=markup)


@router.message(Command("digest"))
async def digest_handler(message: Message) -> None:
    if message.from_user is None:
        return

    with SessionLocal() as session:
        user_service = UserService(session)
        user = user_service.get_by_telegram_user_id(message.from_user.id)
        if user is None:
            user = user_service.upsert_telegram_user(
                telegram_user_id=message.from_user.id,
                username=message.from_user.username or "",
                display_name=message.from_user.full_name,
            )

        ingestion_runs = await IngestionService(session).ingest_user_subscriptions(
            user.id,
            limit=20,
            allow_login=False,
        )
        result = DigestService(session).generate_digest_for_user(user.id)
        if result.digest is not None:
            DigestService(session).mark_delivered(result.digest.id)

    failure_note = _build_ingestion_note(ingestion_runs)
    message_text = result.message_text if not failure_note else f"{result.message_text}\n\n{failure_note}"
    await message.answer(message_text, reply_markup=MAIN_KEYBOARD)


@router.callback_query(F.data == "show-topics")
async def show_topics_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        return

    with SessionLocal() as session:
        topics = CatalogService(session).list_topics()

    await callback.message.edit_text(
        "Curated topics:",
        reply_markup=_build_topics_keyboard(topics),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("show-topic:"))
async def show_topic_channels_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return

    topic_id = int(callback.data.split(":")[1])
    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username or "",
            display_name=callback.from_user.full_name,
        )
        text, markup = _build_channels_view(session, user.id, topic_id=topic_id)

    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle-sub:"))
async def toggle_subscription_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return

    _, channel_id_raw, topic_id_raw = callback.data.split(":")
    channel_id = int(channel_id_raw)
    topic_id = None if topic_id_raw == "0" else int(topic_id_raw)

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username or "",
            display_name=callback.from_user.full_name,
        )
        subscription = SubscriptionService(session).toggle_subscription(user.id, channel_id)
        text, markup = _build_channels_view(session, user.id, topic_id=topic_id)

    state_text = "enabled" if subscription.enabled else "disabled"
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer(f"Subscription {state_text}.")


@router.message(F.text == "Help")
async def help_button_handler(message: Message) -> None:
    await help_handler(message)


@router.message(F.text == "Link account")
async def link_button_handler(message: Message) -> None:
    await link_handler(message)


@router.message(F.text == "Topics")
async def topics_button_handler(message: Message) -> None:
    await topics_handler(message)


@router.message(F.text == "Channels")
async def channels_button_handler(message: Message) -> None:
    await channels_handler(message)


@router.message(F.text == "Digest")
async def digest_button_handler(message: Message) -> None:
    await digest_handler(message)


def _build_topics_keyboard(topics: list) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=topic.name,
                callback_data=f"show-topic:{topic.id}",
            )
        ]
        for topic in topics
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_channels_view(session, user_id: int, topic_id: int | None) -> tuple[str, InlineKeyboardMarkup]:
    catalog_service = CatalogService(session)
    subscription_service = SubscriptionService(session)

    topic = catalog_service.get_topic(topic_id) if topic_id is not None else None
    channels = catalog_service.list_channels(topic_id=topic_id)
    subscription_map = subscription_service.get_subscription_map(user_id)

    if topic is None:
        text = "Curated channels. Tap a button to enable or disable a source."
    else:
        text = f"Channels in topic: {topic.name}"

    keyboard_rows = []
    for channel in channels:
        subscription = subscription_map.get(channel.id)
        status = "ON" if subscription and subscription.enabled else "OFF"
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"[{status}] {channel.title}",
                    callback_data=f"toggle-sub:{channel.id}:{topic_id or 0}",
                )
            ]
        )

    if topic_id is not None:
        keyboard_rows.append(
            [InlineKeyboardButton(text="Back to topics", callback_data="show-topics")]
        )

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def _build_ingestion_note(ingestion_runs: list) -> str:
    failed_runs = [run for run in ingestion_runs if run.status == "failed" and run.error_message]
    if not failed_runs:
        return ""
    return f"Ingestion note: {failed_runs[0].error_message}"
