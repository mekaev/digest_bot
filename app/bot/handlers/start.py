from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.db.session import SessionLocal
from app.ingestion.telegram_client import ChannelValidationError, IngestionConfigurationError
from app.ingestion.service import IngestionService
from app.services.catalog_service import CatalogService
from app.services.digest_service import DigestService
from app.services.subscription_service import SubscriptionService
from app.services.user_channel_service import AddChannelResult, UserChannelService
from app.services.user_service import UserService

router = Router()


class AddChannelState(StatesGroup):
    waiting_for_channel = State()

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Help"),
            KeyboardButton(text="Link account"),
        ],
        [
            KeyboardButton(text="Channels"),
            KeyboardButton(text="Add channel"),
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
    await message.answer(_build_help_text(), reply_markup=MAIN_KEYBOARD)


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


@router.message(Command("addchannel"))
async def add_channel_command_handler(message: Message, state: FSMContext) -> None:
    channel_reference = _extract_command_argument(message.text)
    if channel_reference:
        await _handle_add_channel_submission(message, state, channel_reference)
        return

    await state.set_state(AddChannelState.waiting_for_channel)
    await message.answer(
        "Send a public channel as @username or https://t.me/username.",
        reply_markup=MAIN_KEYBOARD,
    )


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


@router.message(AddChannelState.waiting_for_channel)
async def add_channel_input_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer(
            "Send a public channel as @username or https://t.me/username.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if message.text == "Add channel":
        await message.answer(
            "Send a public channel as @username or https://t.me/username.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if message.text.startswith("/addchannel"):
        channel_reference = _extract_command_argument(message.text)
        if not channel_reference:
            await message.answer(
                "Send a public channel as @username or https://t.me/username.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        await _handle_add_channel_submission(message, state, channel_reference)
        return

    if message.text.startswith("/"):
        await state.clear()
        await message.answer(
            "Channel add was cancelled. Use /addchannel when you want to add a source.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if message.text in {"Help", "Link account", "Channels", "Digest"}:
        await state.clear()
        if message.text == "Help":
            await help_handler(message)
        elif message.text == "Link account":
            await link_handler(message)
        elif message.text == "Channels":
            await channels_handler(message)
        else:
            await digest_handler(message)
        return

    await _handle_add_channel_submission(message, state, message.text)


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


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

    payload = callback.data.split(":")
    _, channel_id_raw, *topic_payload = payload
    channel_id = int(channel_id_raw)
    topic_id_raw = topic_payload[0] if topic_payload else "0"
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


@router.callback_query(F.data.startswith("remove-user-channel:"))
async def remove_user_channel_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return

    _, channel_id_raw = callback.data.split(":")
    channel_id = int(channel_id_raw)

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username or "",
            display_name=callback.from_user.full_name,
        )
        try:
            UserChannelService(session).remove_user_added_channel_for_user(user.id, channel_id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        text, markup = _build_channels_view(session, user.id, topic_id=None)

    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("Channel removed from your sources.")


@router.message(F.text == "Help")
async def help_button_handler(message: Message) -> None:
    await help_handler(message)


@router.message(F.text == "Link account")
async def link_button_handler(message: Message) -> None:
    await link_handler(message)


@router.message(F.text == "Channels")
async def channels_button_handler(message: Message) -> None:
    await channels_handler(message)


@router.message(F.text == "Add channel")
async def add_channel_button_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(AddChannelState.waiting_for_channel)
    await message.answer(
        "Send a public channel as @username or https://t.me/username.",
        reply_markup=MAIN_KEYBOARD,
    )


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
    if topic_id is not None:
        return _build_topic_channels_view(session, user_id, topic_id)

    return _build_main_channels_view(session, user_id)


def _build_main_channels_view(session, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    catalog_service = CatalogService(session)
    subscription_service = SubscriptionService(session)
    user_channel_service = UserChannelService(session)

    curated_channels = catalog_service.list_channels()
    user_channels = user_channel_service.list_user_added_channels(user_id)
    subscription_map = subscription_service.get_subscription_map(user_id)

    lines = [
        "Channels overview.",
        "",
        "Curated channels:",
    ]
    for channel in curated_channels:
        subscription = subscription_map.get(channel.id)
        status = _format_enabled_state(bool(subscription and subscription.enabled))
        lines.append(f"- {status} {channel.title} (@{channel.telegram_handle})")

    lines.extend(
        [
            "",
            "Your channels:",
        ]
    )
    if not user_channels:
        lines.append("- None yet. Use /addchannel to add a public source.")
    else:
        for entry in user_channels:
            status = _format_enabled_state(entry.subscription.enabled)
            lines.append(f"- {status} {entry.channel.title} (@{entry.channel.telegram_handle})")

    keyboard_rows = [
        [InlineKeyboardButton(text="Curated channels", callback_data="noop")]
    ]
    for channel in curated_channels:
        subscription = subscription_map.get(channel.id)
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=_build_toggle_button_text(channel.title, bool(subscription and subscription.enabled)),
                    callback_data=f"toggle-sub:{channel.id}:0",
                )
            ]
        )

    keyboard_rows.append(
        [InlineKeyboardButton(text="Your channels", callback_data="noop")]
    )
    for entry in user_channels:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=_build_toggle_button_text(entry.channel.title, entry.subscription.enabled),
                    callback_data=f"toggle-sub:{entry.channel.id}:0",
                ),
                InlineKeyboardButton(
                    text="Remove",
                    callback_data=f"remove-user-channel:{entry.channel.id}",
                ),
            ]
        )

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def _build_topic_channels_view(session, user_id: int, topic_id: int) -> tuple[str, InlineKeyboardMarkup]:
    catalog_service = CatalogService(session)
    subscription_service = SubscriptionService(session)

    topic = catalog_service.get_topic(topic_id)
    channels = catalog_service.list_channels(topic_id=topic_id)
    subscription_map = subscription_service.get_subscription_map(user_id)
    text = f"Channels in topic: {topic.name}" if topic is not None else "Curated channels."

    keyboard_rows = []
    for channel in channels:
        subscription = subscription_map.get(channel.id)
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=_build_toggle_button_text(channel.title, bool(subscription and subscription.enabled)),
                    callback_data=f"toggle-sub:{channel.id}:{topic_id}",
                )
            ]
        )

    keyboard_rows.append(
        [InlineKeyboardButton(text="Back to topics", callback_data="show-topics")]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def _build_ingestion_note(ingestion_runs: list) -> str:
    failed_runs = [run for run in ingestion_runs if run.status == "failed" and run.error_message]
    if not failed_runs:
        return ""
    return f"Ingestion note: {failed_runs[0].error_message}"


async def _handle_add_channel_submission(
    message: Message,
    state: FSMContext,
    channel_reference: str,
) -> None:
    if message.from_user is None:
        return

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username or "",
            display_name=message.from_user.full_name,
        )
        service = UserChannelService(session)
        try:
            result = await service.add_public_channel_for_user(user.id, channel_reference)
        except ChannelValidationError as exc:
            await message.answer(str(exc), reply_markup=MAIN_KEYBOARD)
            return
        except IngestionConfigurationError as exc:
            await message.answer(str(exc), reply_markup=MAIN_KEYBOARD)
            return

    await state.clear()
    await message.answer(_build_add_channel_success_text(result), reply_markup=MAIN_KEYBOARD)


def _build_add_channel_success_text(result: AddChannelResult) -> str:
    handle = f"@{result.channel.telegram_handle}"
    if result.channel_created:
        return f"Channel {handle} was added and enabled for your digest."
    if result.already_enabled:
        return f"Channel {handle} is already enabled in your sources."
    return f"Channel {handle} is now enabled for your digest."


def _build_help_text() -> str:
    return (
        "Commands:\n"
        "/start - create or refresh your Telegram profile\n"
        "/link - generate a short-lived web link code\n"
        "/channels - manage curated and user-added channel subscriptions\n"
        "/addchannel - add a public Telegram channel by @username or t.me link\n"
        "/digest - fetch posts and send the latest digest"
    )


def _build_toggle_button_text(channel_title: str, enabled: bool) -> str:
    return f"[{_format_enabled_state(enabled)}] {channel_title}"


def _format_enabled_state(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


def _extract_command_argument(text: str | None) -> str:
    if not text:
        return ""
    _command, _separator, argument = text.partition(" ")
    return argument.strip()
