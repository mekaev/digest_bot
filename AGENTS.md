# AGENTS.md

## Project
AI Telegram Digest Bot MVP

## Goal
Build a defendable MVP of a Telegram bot + web/API service that ingests posts from curated public Telegram channels, ranks important content, generates digests, and later supports a RAG assistant.

## Locked architecture
- Modular monolith
- Python
- FastAPI
- aiogram for bot
- Telethon for ingestion
- Together AI for LLM
- SQLAlchemy
- SQLite for MVP start
- VPS deploy over SSH
- No Docker for now
- No microservices

## Do not do
- Do not rewrite architecture without a strong reason
- Do not introduce microservices
- Do not overengineer
- Do not expand scope beyond MVP
- Do not replace SQLite/Postgres choice unless explicitly asked
- Do not touch unrelated modules in one task

## Work style
- First analyze current state briefly
- Then propose the next concrete step
- Keep tasks narrow and verifiable
- One task = one small deliverable
- Prefer point changes over broad refactors
- Preserve existing structure unless there is a real bug

## Source of truth files
- product_scope.md
- architecture.md
- execution_plan.md
- Evolution.md
- README.md
- requirements.txt

## Current priority
Focus on building the MVP in the fixed order from execution_plan.md.
Right now prefer closing the current smallest working slice before moving to the next stage.

## Expected output format
When asked to implement:
1. Brief analysis
2. Concrete plan
3. Code changes
4. What was changed
5. How to run / verify

## If task is complex
- First read relevant files
- Then propose a short plan
- Ask at most 2 clarifying questions only if truly blocked