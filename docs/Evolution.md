# Evolution Log

---

## [2026-04-22 14:40] Telegram assistant follow-ups and anchored RAG filtering
### Current state
- Telegram bot now answers ordinary text questions as assistant queries, not only voice messages.
- Voice and text assistant answers share the same bot-side QA helper and response formatting.
- RAG retrieval now treats known brand/entity tokens such as `google`, `openai`, `claude`, `anthropic`, `kimi`, and `mcp` as anchor terms.
- When a query has anchor terms, unrelated posts without the anchor are filtered out instead of being returned due to generic words like "new", "product", or "model".

### Decisions made
- Kept retrieval SQLite/lexical and did not add embeddings or new tables.
- Treated text messages as fresh assistant questions, not stateful corrections to the previous answer.
- Left existing commands, reply-keyboard buttons, and add-channel state handling ahead of the generic text assistant handler.

### Problems / blockers
- Retrieval is still lexical, so semantic matches without the explicit brand/entity token can be missed.
- The generic text assistant does not store chat history yet.

### Files changed
- `app/bot/handlers/start.py`
- `app/rag/retriever.py`
- `app/rag/qa.py`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- If real usage still shows weak matches, add embeddings or a lightweight reranker behind the existing retriever interface.

### Prompt handoff
Telegram assistant follow-ups now work for normal text messages, and RAG retrieval has anchor filtering for explicit brand/entity queries. Preserve the current modular monolith and SQLite-first retriever; the clean next extension point is still `SQLiteRAGRetriever`, not a bot flow rewrite.

---

## [2026-04-22 00:00] Telegram voice input for RAG assistant
### Current state
- Telegram bot now accepts voice messages as assistant questions.
- The bot downloads the Telegram voice file, transcribes it through a configurable Whisper-compatible STT API, and sends the transcript into the existing `QAService`.
- Voice answers reuse the same stored-post context and citation source format as the current RAG assistant instead of adding a new chat subsystem.
- If STT is not configured or transcription fails, the bot returns a user-facing error and does not crash the update.

### Decisions made
- Kept the slice bot-side and transient: no new DB tables for audio inputs or transcript history yet.
- Reused existing user identity, digest window preference, and RAG retrieval boundaries.
- Added provider configuration through `.env` (`STT_API_KEY`, `STT_API_BASE_URL`, `STT_MODEL`, `STT_LANGUAGE`) without changing the LLM/Together path.

### Problems / blockers
- Real STT quality and latency depend on the external Whisper-compatible provider configured in deployment.
- Telegram voice processing is covered with mocked tests locally; full end-to-end verification still needs a live bot token and network access.

### Files changed
- `app/config.py`
- `app/services/stt.py`
- `app/bot/handlers/start.py`
- `.env.example`
- `README.md`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- Verify on a live Telegram bot with a real STT provider key, then decide whether transcript persistence is needed for analytics/history.

### Prompt handoff
Voice input is now wired into the Telegram-first MVP as a narrow flow: Telegram voice -> temporary audio download -> `STTService` -> `QAService.answer()` -> Telegram reply with transcript, answer, and sources. The next agent should not add audio/transcript tables unless explicitly requested; first validate provider settings and real bot behavior.

---

## [2026-04-17 15:00] Bot add-channel crash fix
### Current state
- Telegram bot add-channel flow now accepts both `@username` and `https://t.me/username` inputs without crashing.
- The Telethon validation boundary normalizes runtime failures into app-level errors so the bot can always answer with a user-friendly message.
- Existing user-added channel management flows still work: enable, disable, remove, and re-add.

### Decisions made
- Kept the fix at the validation boundary instead of changing the bot flow contract or the persistence model.
- Treated raw Telethon/runtime failures as configuration-level issues for the bot surface so the handler can degrade gracefully.
- Added regression coverage for the bot handler path rather than relying only on service-layer tests.

### Problems / blockers
- Telethon still needs a live local session and network access for real validation, so the exact runtime failure can vary outside the sandbox.
- The local test environment cannot fully exercise the live Telegram API, so runtime wrapping is covered by regression tests instead.

### Files changed
- `app/ingestion/telegram_client.py`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- Keep the bot flow stable and move on to the next narrow MVP slice without reworking the add-channel contract again.

### Prompt handoff
The add-channel bug is fixed at the Telethon boundary: the bot now handles `@username` and `https://t.me/...` inputs, and runtime validation failures are converted into user-facing errors instead of crashing the update. The next agent should preserve this contract and only extend the flow if a new narrow slice explicitly requires it.

---

## [2026-04-17 14:10] Minimal web RAG assistant over stored posts
### Current state
- Logged-in web users now have a protected `/app/assistant` page inside the existing server-rendered cabinet.
- The assistant retrieves from already stored `posts` inside the user's own enabled user-added channels and uses the user's own digest items only as a secondary scoring hint.
- Answers are rendered in Russian with `[1]`, `[2]`, `[3]` citation markers plus a small sources block.
- If Together AI is disabled or the request fails, the flow still returns a deterministic grounded fallback answer instead of failing hard.

### Decisions made
- Kept the slice SQLite-first and explainable: retrieval is lexical/hybrid with token overlap, phrase match, channel boost, recency bonus, and digest-item hinting.
- Kept posts as the primary evidence unit so callers can later swap the retrieval backend to embeddings/vector search without changing the assistant route contract.
- Reused the existing FastAPI session auth and Jinja templates instead of adding a new frontend surface or API layer.

### Problems / blockers
- Retrieval is still a first-pass heuristic layer, so semantic paraphrases and multilingual wording can still be missed until embeddings are added later.
- When Together is configured but unreachable, the request still pays the provider-client retry cost before the deterministic fallback is used.

### Files changed
- `app/rag/retriever.py`
- `app/rag/qa.py`
- `app/api/routes/web.py`
- `app/api/templates/base.html`
- `app/api/templates/app_assistant.html`
- `tests/test_mvp_slice.py`
- `README.md`
- `docs/Evolution.md`

### Next step
- Keep the assistant slice narrow and improve retrieval quality only if needed: better token normalization, lightweight synonym support, or a drop-in embedding backend behind the same `QAService`/retriever interface.

### Prompt handoff
There is now a minimal but working web RAG assistant in the existing cabinet. It is intentionally SQLite-first and deterministic: `SQLiteRAGRetriever` enforces user boundaries and returns citation metadata, `QAService` builds a grounded Russian answer with a hard fallback path, and `/app/assistant` renders it through the current Jinja flow. The next agent should not rewrite the architecture or add vector infra unless explicitly asked; the clean extension point is the retriever implementation behind the existing service contract.

---

## [2026-04-17 13:35] Digest ranking normalization + topic dedup cleanup
### Current state
- Digest ranking no longer relies only on absolute engagement numbers across all channels.
- Candidate posts are now scored against a per-channel baseline inside the selected digest window.
- Digest selection now removes repeated coverage of the same news topic across channels before top-N truncation.
- Digest selection also does a first-pass channel diversity sweep so one source does not crowd out all others when multiple channels have relevant posts.

### Decisions made
- Kept the existing ingest and persistence model; no schema or architecture rewrite was introduced for this cleanup.
- Used simple explainable heuristics instead of embeddings or RAG: channel-relative engagement, token-based topic similarity, and two-pass item selection.
- Left the existing Russian-only digest prompt/fallback path in place and fixed the main quality issue in ranking/selection instead of adding a bigger generation subsystem.

### Problems / blockers
- Topic dedup is still heuristic and lexical, so it will not catch every semantic duplicate and may need later tuning on real user data.
- There is still no source-of-truth "original post" signal, so when several channels cover the same news the winner is the best-ranked representative, not guaranteed the earliest source.

### Files changed
- `app/digest/ranking.py`
- `app/services/digest_service.py`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- Tighten digest selection quality further with lightweight novelty/originality signals or better duplicate clustering if real traffic shows remaining repetition.

### Prompt handoff
The digest path now uses per-channel relative ranking, topic-level duplicate filtering across channels, and a simple diversity-first top-N selector. The next cleanup should stay narrow: tune ranking weights or duplicate detection from real examples instead of introducing RAG, a new frontend, or a rewritten architecture.

---

## [2026-04-17 13:20] User-added-only bot flow + digest period and ranking
### Current state
- Curated channels are removed from the visible bot UX and `/channels` now shows only user-added channels for the current user.
- Bot users can manage only their own user-added sources in the current Telegram flow: enable, disable, remove, and add new public channels.
- A digest period selector is now available with three fixed options: `1 day`, `3 days`, and `7 days`, with `7 days` as the default.
- Digest generation now filters posts by the selected window, ranks candidates, and truncates output to top `5` items.
- Ranking now uses simple engagement plus recency heuristics over views, reactions, forwards, comments, and age, while missing metrics safely fall back to `0`.

### Decisions made
- Kept the existing modular monolith and reused `DigestSchedule` to store `window_days` instead of introducing a new preference table.
- Left curated catalog data in the database and services for compatibility, but removed it from the active bot product path.
- Reused the existing Telethon ingest path and only narrowed the `/digest` bot scenario to enabled user-added channels.
- Added lightweight post metric columns directly to `posts` so ranking stays simple and explainable.

### Problems / blockers
- Existing curated data is still seeded for compatibility and web surface coverage, but it is intentionally ignored in the current bot digest flow.
- If Telegram does not expose one of the engagement fields for a message, ranking degrades gracefully but becomes less informative for that post.
- RAG and broader web polish are still out of scope for this slice.

### Files changed
- `app/bot/handlers/start.py`
- `app/db/models.py`
- `app/db/session.py`
- `app/digest/ranking.py`
- `app/ingestion/service.py`
- `app/ingestion/telegram_client.py`
- `app/services/digest_service.py`
- `app/services/user_channel_service.py`
- `app/services/user_service.py`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- Build either RAG over stored posts/digests or a small web polish slice on top of the new user-added-only digest workflow.

### Prompt handoff
The Telegram-first MVP now treats user-added public channels as the only bot-facing source model. `/channels` is user-added-only, `/period` stores a digest window of `1d/3d/7d`, `/digest` uses only enabled user-added channels within that window, and ranking/top-N selection are now part of the generation path. The next slice should either add RAG over stored posts/digests or improve the web surface without reintroducing curated bot UX, Topics, or a new frontend architecture.

---

## [2026-04-17 12:40] User-added public channels + bot UX cleanup
### Current state
- User-added public Telegram channels are already supported through the existing Telethon validation layer.
- `/channels` now shows two explicit sections: curated channels first, then user-added channels for the current user.
- User-added channels can be toggled on and off from the same bot view, and they can be removed from the personal list without touching curated catalog data.
- Topics are no longer exposed in the main bot UX because the flow is premature and adds confusion.
- Digest generation now uses a stricter Russian-only prompt contract with a stable compact format and fallback validation.

### Decisions made
- Kept the modular monolith and current persistence model; no topic/schema rewrite was introduced for this cleanup.
- Preserved curated catalog behavior and separated user-added sources only at the bot presentation layer.
- Left topic data structures in place, but removed Topics from the reply keyboard and help text instead of expanding the flow.
- Strengthened the LLM prompt with explicit language control, format requirements, and top-N output limits instead of changing ingest.

### Problems / blockers
- If the LLM is disabled entirely, fallback formatting stays stable but cannot fully translate mixed-language source text into Russian.
- Old hidden `/topics` handlers still exist for compatibility, but they are intentionally not part of the main UX anymore.
- User-added channel removal currently removes the current user's subscription entry, not the shared channel row from the database.

### Files changed
- `app/bot/handlers/start.py`
- `app/services/digest_service.py`
- `app/services/llm.py`
- `app/services/user_channel_service.py`
- `tests/test_mvp_slice.py`
- `docs/Evolution.md`

### Next step
- Implement digest window selection (`1d` / `3d` / `7d`) and polish ranking/top-N behavior on top of the current stable Telegram-first flow.

### Prompt handoff
The working MVP now supports curated channels plus user-added public channels validated via Telethon. Bot UX was cleaned up so `/channels` clearly separates curated sources from personal sources, Topics were removed from the visible bot flow, and digest prompting was tightened to produce a more stable Russian-only summary format. The next narrow slice should focus on digest window control (`1d/3d/7d`) and ranking/top-N polish without rewriting architecture, adding RAG, or expanding topic management.

Этот файл нужен как краткая память проекта.
Его задача - фиксировать текущее состояние, решения, проблемы и следующий шаг так, чтобы новый чат или новый агент мог быстро продолжить работу без потери контекста.

---

## [2026-04-16 14:30] Minimal web surface via link-code auth
### Current state
- Добавлен минимальный server-rendered web surface внутри FastAPI без React/Next.
- Появились публичные страницы `/` и `/login`, а также protected pages `/app`, `/app/digests`, `/app/subscriptions`.
- Web auth теперь работает через существующий Telegram link code: redeem помечает код использованным и создает signed session cookie.
- Пользователь может смотреть свой профиль, сохраненные digests и управлять curated subscriptions из web UI.
- Для web routes добавлены integration tests; полный `tests/test_mvp_slice.py` проходит локально в `.venv`.

### Decisions made
- Не трогать Telethon ingest, bot flow, scheduler, RAG, voice и analytics в этом slice.
- Оставить HTML pages отдельно от существующего JSON API; `catalog` и `subscriptions/{user_id}` не переписывались под auth.
- Не тянуть внешнюю session dependency: вместо Starlette SessionMiddleware добавлен локальный signed-cookie middleware без изменения архитектуры.
- Не добавлять новые таблицы; использованы текущие `users`, `telegram_link_codes`, `subscriptions`, `digests`.

### Problems / blockers
- Для локального прогона web routes в `.venv` пришлось доустановить `jinja2`; зависимость добавлена в `requirements.txt`.
- SQLite возвращает часть datetime как naive, поэтому redeem flow нормализует `expires_at` в UTC перед проверкой.

### Files changed
- `app/api/main.py`
- `app/api/routes/web.py`
- `app/api/session_middleware.py`
- `app/api/templates/*`
- `app/config.py`
- `app/db/session.py`
- `app/services/user_service.py`
- `app/services/digest_service.py`
- `.env.example`
- `requirements.txt`
- `tests/test_mvp_slice.py`

### Next step
- Следующий узкий slice можно брать уже поверх рабочего web auth surface: либо scheduler/delivery path, либо дальнейшее добивание demo path по execution plan без расширения scope.

### Prompt handoff
Telegram-first baseline больше не только bot-side: теперь есть минимальный FastAPI web cabinet с landing, link-code auth, profile, digest history и subscriptions. Следующий агент не должен переписывать архитектуру и не должен менять auth-модель. Можно опираться на существующие `UserService.redeem_link_code`, signed session cookie middleware и server-rendered templates.

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
