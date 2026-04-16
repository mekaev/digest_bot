from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

router = Router()

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Help"),
            KeyboardButton(text="Link account"),
        ]
    ],
    resize_keyboard=True,
)


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "Hi! This is the AI Telegram Digest Bot MVP.\n"
        "Use /help or the buttons below.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Available commands:\n"
        "/start - start the bot\n"
        "/help - show help\n"
        "/link - link account (stub)",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("link"))
@router.message(F.text & (F.text.casefold() == "link account"))
async def link_handler(message: Message) -> None:
    await message.answer("Скоро будет доступно.")


@router.message(F.text & (F.text.casefold() == "help"))
async def help_button_handler(message: Message) -> None:
    await help_handler(message)
