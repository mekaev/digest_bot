# Evolution Log

Этот файл нужен как краткая память проекта.
Его задача - фиксировать текущее состояние, решения, проблемы и следующий шаг так, чтобы новый чат или новый агент мог быстро продолжить работу без потери контекста.

---

## Правила ведения
Каждое обновление добавляется новым блоком сверху.

Шаблон записи:

```md
## [YYYY-MM-DD HH:MM] Stage / Topic
### Current state
- что уже сделано

### Decisions made
- какие решения приняты
- почему

### Problems / blockers
- что мешает
- какие есть гипотезы

### Files changed
- список файлов

### Next step
- один ближайший конкретный шаг

### Prompt handoff
Краткий текст для следующего агента / чата:
...
```

---

## [2026-04-15 00:00] Project initialization
### Current state
- Сформулирована идея MVP: Telegram-бот и web UI для персональных дайджестов по открытым Telegram-каналам.
- Зафиксированы четыре базовых документа: scope, architecture, execution plan, evolution log.
- Принято решение строить MVP как модульный монолит.

### Decisions made
- Основной продуктовый scope ограничен curated open channels.
- Self-serve импорт каналов не является обязательной частью ядра MVP.
- Возможный folder-import рассматривается как MVP+ расширение.
- Для bot layer выбран aiogram.
- Для ingestion layer выбран Telethon.
- Для AI provider выбран Together AI.
- Для деплоя выбран SSH/VPS-подход без обязательного Docker на старте.

### Problems / blockers
- Самый рискованный участок - ingestion Telegram-каналов.
- Не выбраны точные модели Together AI для summary, embeddings и STT.
- Нужно быстро собрать repo skeleton и начать реализацию P0.

### Files changed
- product_scope.md
- architecture.md
- execution_plan.md
- Evolution.md

### Next step
- Создать repo skeleton и базовую структуру модулей `web`, `api`, `bot`, `docs`.

### Prompt handoff
Нужно продолжить проект AI Digest Assistant. Уже есть 4 markdown-файла с scope, architecture, execution plan и evolution log. Следующий шаг - создать repo skeleton для modular monolith: web, api, bot, docs, базовые env-файлы, README и стартовую структуру backend/frontend. Важно не расширять scope beyond MVP и не уходить в overengineering.

## 2026-04-15

### Done
- создан skeleton проекта
- настроен git и GitHub
- создан .venv
- FastAPI health endpoint запускается

### Decisions
- bot: aiogram
- ingestion: Telethon
- LLM: Together AI
- DB: SQLite for MVP
- deploy: VPS over SSH, no Docker for now

### Issues
- был конфликт import path для app
- git remote history conflict решен force push

### Next
- заполнить .env
- привести config.py к рабочему baseline
- поднять bot /start