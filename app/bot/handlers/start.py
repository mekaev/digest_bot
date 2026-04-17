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
from app.services.digest_service import DigestService
from app.services.user_channel_service import AddChannelResult, UserChannelService
from app.services.user_service import ALLOWED_DIGEST_WINDOW_DAYS, UserService

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
        [
            KeyboardButton(text="Period"),
            KeyboardButton(text="Digest"),
        ],
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
        channel_entries = UserChannelService(session).list_user_added_channels(user.id)
        has_enabled_channels = any(entry.subscription.enabled for entry in channel_entries)

    next_step = "/addchannel" if not has_enabled_channels else "/digest"
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
    await message.answer(
        "Topics are disabled in the current MVP. Use /channels, /addchannel, and /period.",
        reply_markup=MAIN_KEYBOARD,
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
        text, markup = _build_channels_view(session, user.id)

    await message.answer(text, reply_markup=markup)


@router.message(Command("period"))
async def period_handler(message: Message) -> None:
    if message.from_user is None:
        return

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username or "",
            display_name=message.from_user.full_name,
        )
        window_days = UserService(session).get_digest_window_days(user.id)

    await message.answer(
        _build_period_text(window_days),
        reply_markup=_build_period_keyboard(window_days),
    )


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

        window_days = user_service.get_digest_window_days(user.id)
        ingestion_runs = await IngestionService(session).ingest_user_subscriptions(
            user.id,
            limit=20,
            allow_login=False,
            user_added_only=True,
        )
        result = DigestService(session).generate_digest_for_user(user.id)
        if result.digest is not None:
            DigestService(session).mark_delivered(result.digest.id)

    failure_note = _build_ingestion_note(ingestion_runs)
    period_note = f"Digest window: {window_days} day(s)."
    message_text = result.message_text
    if failure_note:
        message_text = f"{message_text}\n\n{failure_note}"
    await message.answer(f"{period_note}\n\n{message_text}", reply_markup=MAIN_KEYBOARD)


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

    if message.text == "Period" or message.text.startswith("/period"):
        await state.clear()
        await period_handler(message)
        return

    if message.text == "Channels" or message.text.startswith("/channels"):
        await state.clear()
        await channels_handler(message)
        return

    if message.text == "Help" or message.text.startswith("/help"):
        await state.clear()
        await help_handler(message)
        return

    if message.text == "Link account" or message.text.startswith("/link"):
        await state.clear()
        await link_handler(message)
        return

    if message.text == "Digest" or message.text.startswith("/digest"):
        await state.clear()
        await digest_handler(message)
        return

    if message.text.startswith("/"):
        await state.clear()
        await message.answer(
            "Channel add was cancelled. Use /addchannel when you want to add a source.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await _handle_add_channel_submission(message, state, message.text)


@router.callback_query(F.data == "show-topics")
async def show_topics_callback(callback: CallbackQuery) -> None:
    await callback.answer("Topics are disabled in the current MVP.", show_alert=True)


@router.callback_query(F.data.startswith("show-topic:"))
async def show_topic_channels_callback(callback: CallbackQuery) -> None:
    await callback.answer("Topics are disabled in the current MVP.", show_alert=True)


@router.callback_query(F.data.startswith("toggle-sub:"))
async def toggle_subscription_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return

    payload = callback.data.split(":")
    if len(payload) < 2:
        await callback.answer("Unknown channel action.", show_alert=True)
        return
    channel_id = int(payload[1])

    with SessionLocal() as session:
        user = UserService(session).upsert_telegram_user(
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username or "",
            display_name=callback.from_user.full_name,
        )
        try:
            subscription = UserChannelService(session).toggle_user_channel(user.id, channel_id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        text, markup = _build_channels_view(session, user.id)

    state_text = "enabled" if subscription.enabled else "disabled"
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer(f"Channel {state_text}.")


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
        text, markup = _build_channels_view(session, user.id)

    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("Channel removed from your list.")


@router.callback_query(F.data.startswith("set-period:"))
async def set_period_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return

    _, raw_days = callback.data.split(":")
    window_days = int(raw_days)

    with SessionLocal() as session:
        user_service = UserService(session)
        user = user_service.upsert_telegram_user(
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username or "",
            display_name=callback.from_user.full_name,
        )
        try:
            user_service.set_digest_window_days(user.id, window_days)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        current_days = user_service.get_digest_window_days(user.id)

    await callback.message.edit_text(
        _build_period_text(current_days),
        reply_markup=_build_period_keyboard(current_days),
    )
    await callback.answer(f"Digest period set to {current_days} day(s).")


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


@router.message(F.text == "Period")
async def period_button_handler(message: Message) -> None:
    await period_handler(message)


@router.message(F.text == "Digest")
async def digest_button_handler(message: Message) -> None:
    await digest_handler(message)


def _build_channels_view(session, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    channel_entries = UserChannelService(session).list_user_added_channels(user_id)
    if not channel_entries:
        return (
            "Your channels list is empty.\nUse /addchannel to add a public Telegram source.",
            InlineKeyboardMarkup(inline_keyboard=[]),
        )

    lines = ["Your channels:"]
    keyboard_rows = []
    for entry in channel_entries:
        status = _format_enabled_state(entry.subscription.enabled)
        lines.append(f"- {status} {entry.channel.title} (@{entry.channel.telegram_handle})")
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=_build_toggle_button_text(entry.channel.title, entry.subscription.enabled),
                    callback_data=f"toggle-sub:{entry.channel.id}",
                ),
                InlineKeyboardButton(
                    text="Remove",
                    callback_data=f"remove-user-channel:{entry.channel.id}",
                ),
            ]
        )

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def _build_period_text(window_days: int) -> str:
    return (
        f"Current digest period: {_format_period_label(window_days)}.\n"
        "Your digest will use only enabled user-added channels in this time window."
    )


def _build_period_keyboard(current_days: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_build_period_button_label(days, current_days),
                    callback_data=f"set-period:{days}",
                )
                for days in ALLOWED_DIGEST_WINDOW_DAYS
            ]
        ]
    )


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
        "/channels - manage your user-added channels\n"
        "/addchannel - add a public Telegram channel by @username or t.me link\n"
        "/period - choose the digest period: 1, 3, or 7 days\n"
        "/digest - fetch recent posts from your enabled channels and send the latest digest"
    )


def _build_toggle_button_text(channel_title: str, enabled: bool) -> str:
    return f"[{_format_enabled_state(enabled)}] {channel_title}"


def _format_enabled_state(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


def _build_period_button_label(days: int, current_days: int) -> str:
    prefix = "* " if days == current_days else ""
    return f"{prefix}{_format_period_label(days)}"


def _format_period_label(days: int) -> str:
    return "1 day" if days == 1 else f"{days} days"


def _extract_command_argument(text: str | None) -> str:
    if not text:
        return ""
    _command, _separator, argument = text.partition(" ")
    return argument.strip()
