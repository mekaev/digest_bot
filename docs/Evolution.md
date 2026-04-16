# Evolution Log

Этот файл нужен как краткая память проекта.
Его задача - фиксировать текущее состояние, решения, проблемы и следующий шаг так, чтобы новый чат или новый агент мог быстро продолжить работу без потери контекста.

---

## [2026-04-16 13:35] First real Telegram digest demo works
### Current state
- Подтвержден рабочий Telegram-first demo path на реальных данных.
- Telethon session авторизована локально через телефон.
- Manual ingestion отрабатывает и сохраняет посты в SQLite.
- Бот успешно доставляет непустой digest в Telegram.
- Curated subscriptions, digest generation и delivery работают как единый flow.
- Milestone "first path to first digest" фактически закрыт локально.

### Decisions made
- Текущий vertical slice считаем рабочим baseline и не переписываем.
- SQLite остается локальной runtime DB до следующего milestone.
- Telethon authorization остается через manual bootstrap/session.
- Следующий этап - не переписывание ingest, а развитие продукта поверх уже рабочего baseline.

### Problems / blockers
- Demo path подтвержден локально, но еще не упакован в более удобный operator flow.
- Нужно решить, какой следующий slice делать первым: web-side profile/history или scheduler/manual UX polish.
- Нельзя коммитить секреты и локальные runtime артефакты (.env, Telethon session, SQLite db).

### Files changed
- docs/Evolution.md

### Next step
- Зафиксировать текущий рабочий baseline в git и перейти в новый чат для планирования следующего slice поверх уже рабочего Telegram-first digest path.

### Prompt handoff
Первый реальный digest path уже работает end-to-end локально: Telethon ingest -> posts in SQLite -> digest generation -> Telegram delivery. Следующий агент не должен заново стабилизировать этот flow и не должен переписывать архитектуру. Нужно выбрать и реализовать следующий узкий slice поверх текущего baseline.

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

## [2026-04-16 14:10] Telegram-first path to first digest
### Current state
- Реализован первый вертикальный slice вокруг Telegram-first сценария.
- БД расширена до core-сущностей: users, link codes, topics, channels, subscriptions, schedules, posts, ingestion runs, digests, digest items.
- Добавлен bootstrap `create_all + seed catalog` для SQLite.
- Бот теперь умеет `/start`, `/help`, `/link`, `/topics`, `/channels`, `/digest`.
- Curated catalog загружается из локального seed-файла и доступен через bot flow и API.
- Реализован Telethon-backed ingestion service с ручным entrypoint `scripts/ingest_once.py`.
- Реализован digest generation service: отбор постов, дедуп, сохранение digest и отправка текста в Telegram.
- Добавлены минимальные API endpoints для чтения catalog и чтения/сохранения subscriptions.
- Добавлены unit tests на schema bootstrap, link code reuse, subscription validation, ingestion store logic и digest generation.

### Decisions made
- Выбран Telegram-first identity model: пользователь создается по `telegram_user_id`, а web-link code строится поверх него.
- Catalog пока только curated и repo-backed, без admin UI и без user-added sources.
- Scheduler, web auth, history, RAG, voice и analytics оставлены за рамками этого slice.
- Telethon authorization для первой сессии вынесена в manual script, а bot-side digest использует уже существующую session.

### Problems / blockers
- Без заранее авторизованной Telethon session бот не сможет сам выполнить реальный ingest через `/digest`; для первого запуска нужен `python scripts/ingest_once.py`.
- В текущей среде нет прямого сетевого доступа к Telegram API, поэтому end-to-end ingest/polling здесь не проверяется.
- `.env.example` уже был изменен локально до этой задачи и не включался в этот slice автоматически.

### Files changed
- `app/bootstrap.py`
- `app/catalog_seed.json`
- `app/db/models.py`
- `app/db/session.py`
- `app/services/catalog_service.py`
- `app/services/subscription_service.py`
- `app/services/user_service.py`
- `app/services/digest_service.py`
- `app/ingestion/telegram_client.py`
- `app/ingestion/service.py`
- `app/digest/ranking.py`
- `app/bot/main.py`
- `app/bot/handlers/start.py`
- `app/api/main.py`
- `app/api/routes/catalog.py`
- `app/api/routes/subscriptions.py`
- `scripts/ingest_once.py`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- Поднять следующий slice поверх уже готовой persistence-модели: web-side profile/history или стабилизация manual Telethon ingest на реальных curated channels.

### Prompt handoff
Первый Telegram-first slice уже собран: user/link/subscriptions/catalog/ingestion/digest. Следующий агент не должен переписывать архитектуру. Нужно либо добить web-side profile/history поверх текущих `users/subscriptions/digests`, либо стабилизировать реальный ingest и delivery demo path на авторизованной Telethon session.

## [2026-04-16 12:50] Bot bootstrap MVP
### Current state
- Реализован минимальный рабочий `aiogram` bootstrap для Telegram-бота.
- Команды `/start` и `/help` отвечают короткими сообщениями.
- Добавлена reply keyboard с кнопками `Help` и `Link account`.
- `BOT_TOKEN` читается из `.env` через `pydantic-settings` и валидируется при старте.
- `scripts/run_bot.py` запускает бота одной командой.

### Decisions made
- Не трогать архитектуру MVP и не добавлять Telethon, БД, webhook, scheduler или Docker.
- Свести bot layer к одному роутеру и одному handler-модулю для минимального старта.
- Считать отсутствие или пустоту `BOT_TOKEN` фатальной ошибкой конфигурации.

### Problems / blockers
- В этой среде нет сетевого доступа к Telegram API, поэтому полноценный polling smoke-test невозможен.
- Локальная проверка ограничена синтаксической компиляцией и импортами из `.venv`.

### Files changed
- `app/config.py`
- `app/bot/main.py`
- `app/bot/handlers/start.py`
- `app/bot/handlers/__init__.py`
- `scripts/run_bot.py`

### Next step
- Подключить следующий минимальный slice из execution plan после этого bot bootstrap.

### Prompt handoff
Минимальный bot bootstrap уже есть. Следующий агент должен продолжать по execution_plan.md, не ломая текущую структуру: не добавлять Telethon/DB/webhook, а двигаться к следующему маленькому рабочему шагу MVP.

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
- `product_scope.md`
- `architecture.md`
- `execution_plan.md`
- `Evolution.md`

### Next step
- Создать repo skeleton и базовую структуру модулей `web`, `api`, `bot`, `docs`.

### Prompt handoff
Нужно продолжить проект AI Digest Assistant. Уже есть 4 markdown-файла со scope, architecture, execution plan и evolution log. Следующий шаг - создать repo skeleton для modular monolith: web, api, bot, docs, базовые env-файлы, README и стартовую структуру backend/frontend. Важно не расширять scope beyond MVP и не уходить в overengineering.

## 2026-04-15

### Done
- создан skeleton проекта
- настроен git и GitHub
- создан `.venv`
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
- заполнить `.env`
- привести `config.py` к рабочему baseline
- поднять bot `/start`
