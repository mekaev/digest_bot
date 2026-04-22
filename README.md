# AI Telegram Digest Bot

MVP-skeleton for a Telegram bot that ingests channel messages, ranks/summarizes them, and exposes a small API layer.

## Stack

- Python 3.11+
- aiogram (bot)
- FastAPI (API)
- SQLAlchemy (DB layer)
- Together AI client wrapper (LLM service)

## Project structure

- `app/bot` - bot bootstrap and command handlers
- `app/api` - FastAPI application and routes
- `app/ingestion` - message normalization and channel import flows
- `app/digest` - ranking and digest assembly logic
- `app/rag` - simple retrieval and QA orchestration
- `app/services` - integrations (LLM, STT)
- `app/db` - SQLAlchemy base/session/models
- `app/analytics` - lightweight event tracking
- `scripts` - local run entrypoints
- `data` - runtime data (SQLite DB)
- `tests` - test package root
- `docs` - product and architecture documentation

## Quick start

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` values, especially `BOT_TOKEN` and `TOGETHER_API_KEY`.
For Telegram voice questions, configure the Together STT provider:
`STT_API_KEY`, `STT_API_BASE_URL`, `STT_MODEL`, and `STT_LANGUAGE`.

## Run

```bash
python scripts/run_api.py
python scripts/run_bot.py
```

API health check: `GET /health`

## Web cabinet

- Redeem a Telegram `/link` code at `/login` to open the server-rendered cabinet.
- Main protected pages are `/app`, `/app/digests`, `/app/subscriptions`, and `/app/assistant`.
- `/app/assistant` answers follow-up questions over the logged-in user's own enabled channels and saved digests, with citations.
- If Together AI is disabled or unavailable, the assistant falls back to a deterministic snippet-based answer so the MVP remains demoable.

## Telegram voice questions

- Send a Telegram voice message to the bot to ask the assistant about stored posts from your enabled user-added channels.
- The bot downloads the voice file, transcribes it through the configured Together-compatible STT API, then sends the transcript into the same RAG assistant used by `/app/assistant`.
- Use `STT_API_BASE_URL=https://api.together.ai/v1`, `STT_MODEL=openai/whisper-large-v3`, and a Together key in `STT_API_KEY`.
- If `STT_API_KEY` is empty, the service falls back to `TOGETHER_API_KEY`; without either key the bot returns a user-facing configuration error instead of crashing.

