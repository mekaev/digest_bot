import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.bootstrap import bootstrap_application
from app.db.session import SessionLocal
from app.ingestion.service import IngestionService
from app.services.catalog_service import CatalogService


async def _run(handles: list[str], limit: int) -> None:
    bootstrap_application()

    with SessionLocal() as session:
        catalog_service = CatalogService(session)
        channels = catalog_service.list_channels()
        if handles:
            selected = {handle.lstrip("@").lower() for handle in handles}
            channels = [
                channel
                for channel in channels
                if channel.telegram_handle.lstrip("@").lower() in selected
            ]

        runs = await IngestionService(session).ingest_channels(
            channels,
            limit=limit,
            allow_login=True,
        )

    for run in runs:
        print(
            f"channel_id={run.channel_id} status={run.status} "
            f"fetched={run.fetched_count} stored={run.stored_count} error={run.error_message}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest curated Telegram channels once.")
    parser.add_argument("--handle", action="append", default=[], help="Specific channel handle to ingest.")
    parser.add_argument("--limit", type=int, default=20, help="How many messages to fetch per channel.")
    args = parser.parse_args()
    asyncio.run(_run(args.handle, args.limit))


if __name__ == "__main__":
    main()
