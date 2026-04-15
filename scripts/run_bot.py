import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import asyncio

from app.bot.main import run_bot


if __name__ == '__main__':
    asyncio.run(run_bot())
