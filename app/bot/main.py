import asyncio

from aiogram import Bot, Dispatcher

from app.bot.handlers import router as handlers_router
from app.config import get_settings
from app.logging_config import configure_logging


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers_router)
    return dispatcher


async def run_polling() -> None:
    settings = get_settings()
    configure_logging(settings)

    bot = Bot(token=settings.bot_token)
    dispatcher = build_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


async def run_bot() -> None:
    await run_polling()


if __name__ == "__main__":
    asyncio.run(run_polling())
