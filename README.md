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

## Run

```bash
python scripts/run_api.py
python scripts/run_bot.py
```

API health check: `GET /health`

