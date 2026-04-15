from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command('help'))
async def help_handler(message: Message) -> None:
    await message.answer(
        'Commands:\n'
        '/start - initialize bot\n'
        '/help - show this help'
    )
