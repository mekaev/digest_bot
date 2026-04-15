from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router()


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        'Hello! I am AI Telegram Digest Bot. '
        'I can collect messages and prepare concise digests.'
    )
