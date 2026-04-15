import asyncio

from aiogram import Bot, Dispatcher

from app.bot.handlers.help import router as help_router
from app.bot.handlers.start import router as start_router
from app.config import get_settings
from app.logging_config import configure_logging


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(help_router)
    return dispatcher


async def run_bot() -> None:
    settings = get_settings()
    configure_logging(settings)

    if not settings.bot_token:
        raise RuntimeError('BOT_TOKEN is empty. Set BOT_TOKEN in .env file.')

    bot = Bot(token=settings.bot_token)
    dispatcher = build_dispatcher()
    await dispatcher.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(run_bot())
