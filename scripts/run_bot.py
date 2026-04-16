import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.bot.main import run_polling


def main() -> None:
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
