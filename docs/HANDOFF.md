# HANDOFF

## Current stage
Baseline setup: config + bot start

## Current goal
Get aiogram bot with /start working from .env

## Decisions locked
- aiogram for bot
- Telethon for ingestion
- Together AI for LLM
- SQLite for MVP
- VPS deploy, no Docker for now

## Project tree
[короткое дерево]

## Done
- skeleton created
- repo pushed
- api health works

## Blockers
- .env not finalized
- bot not launched yet

## Files to inspect first
- app/config.py
- app/bot/main.py
- app/bot/handlers/start.py
- scripts/run_bot.py

## Next best step
Implement working /start flow and validate BOT_TOKEN config.