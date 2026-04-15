# AI Digest Assistant - Architecture

## 1. Архитектурный принцип
Для MVP используется **модульный монолит**.

Почему не микросервисы:
- мало времени;
- выше сложность деплоя и отладки;
- слишком много инфраструктурной нагрузки для учебного проекта.

Почему не "всё в одном файле":
- нужно масштабировать фичи;
- нужен понятный пайплайн;
- нужен контроль за ingestion, jobs, RAG и аналитикой.

## 2. High-level компоненты
1. **Landing / Web UI**
2. **Backend API**
3. **Telegram Bot**
4. **Background Jobs / Scheduler**
5. **Database**
6. **LLM / Embeddings / STT providers**
7. **Analytics layer**

## 3. Рекомендуемый стек
### Frontend
- Next.js
- Tailwind
- shadcn/ui

### Backend
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic

### Telegram
- **aiogram** для Bot API-части
- **Telethon** для ingestion открытых каналов, если нужен доступ через MTProto-клиента

### Jobs
- APScheduler для простого расписания на старте
- при росте: Celery + Redis

### Database
- PostgreSQL
- pgvector

### AI stack
- Together AI API
- одна текстовая модель для summary / chat / relevance scoring
- одна embedding-модель
- Whisper-compatible STT или отдельный speech-to-text provider

### Analytics
- PostHog или своя таблица events + простой internal dashboard

### Deploy
- backend, bot, scheduler и БД на одном VPS через SSH
- landing / static frontend можно выкладывать на GitHub Pages только если это чистый static build
- динамический web UI / mini app лучше держать на сервере, а не на GitHub Pages

## 4. Почему aiogram + Telethon, а не только одна библиотека
### aiogram
Нужен для bot flow:
- команды;
- callback buttons;
- webhook / polling;
- удобная логика обработки апдейтов.

### Telethon
Нужен, если ingestion реально завязан на чтение открытых каналов через клиентский API.

### Практический выбор
Для MVP лучше не спорить религиозно и не выбирать "одну библиотеку на всё".

**Рекомендация:**
- bot layer: aiogram;
- ingestion layer: Telethon.

Так ты не ломаешься об ограничения Bot API там, где нужен клиентский доступ, и не усложняешь ботовую часть там, где Bot API удобнее.

## 5. Основные модули
### 5.1 Auth & Users
Ответственность:
- авторизация;
- профиль пользователя;
- привязка Telegram;
- настройки.

Сущности:
- users
- auth_accounts
- telegram_links
- user_preferences

### 5.2 Channel Catalog
Ответственность:
- хранение списка поддерживаемых каналов;
- темы каналов;
- статусы доступности;
- импорт подборок.

Сущности:
- channels
- topics
- channel_topics
- folder_imports

### 5.3 Ingestion
Ответственность:
- чтение постов;
- нормализация;
- дедупликация;
- запись в БД;
- логирование ошибок.

Сущности:
- posts
- post_metrics
- ingestion_runs
- source_sync_logs

### 5.4 Ranking & Digest Generation
Ответственность:
- отбор значимых постов;
- relevance scoring;
- дедуп похожих постов;
- генерация summary.

Сущности:
- ranking_runs
- digest_jobs
- digests
- digest_items

### 5.5 RAG
Ответственность:
- chunking;
- embeddings;
- retrieval;
- generation;
- citations.

Сущности:
- document_chunks
- embeddings
- rag_conversations
- rag_messages
- citations

### 5.6 Voice / STT
Ответственность:
- прием аудио;
- транскрибация;
- передача текста в RAG/chat.

Сущности:
- audio_inputs
- transcripts

### 5.7 Analytics
Ответственность:
- трекинг событий;
- usage metrics;
- funnel.

Сущности:
- events
- daily_metrics
- funnel_snapshots

## 6. Основной пользовательский пайплайн
1. User visits landing.
2. User logs in.
3. User connects Telegram.
4. User selects topics/channels/frequency.
5. Scheduler creates digest job.
6. Ingestion collects posts for chosen channels and period.
7. Ranking selects top items.
8. LLM builds digest.
9. Digest is saved to DB.
10. Digest is delivered to Telegram and shown in web history.
11. User asks RAG-assistant questions by text or voice.

## 7. Ingestion pipeline
### Input
- поддерживаемые открытые Telegram-каналы;
- опционально folder-import как источник выбора каналов.

### Stages
1. fetch raw posts
2. normalize text
3. extract metadata
4. deduplicate
5. store post and metrics
6. create chunks
7. generate embeddings

### Notes
- сначала лучше не делать continuous crawling;
- достаточно periodic sync по расписанию;
- важно хранить raw_text и cleaned_text отдельно.

## 8. Ranking pipeline
Базовая версия без сложного research:

`score = engagement + recency + topic_match + llm_relevance - duplicate_penalty`

Где:
- engagement - просмотры / реакции / forwards;
- recency - свежесть;
- topic_match - соответствие интересам пользователя;
- llm_relevance - короткий LLM-scoring, стоит ли брать пост в digest;
- duplicate_penalty - штраф за повтор темы.

## 9. Digest generation pipeline
1. выбрать период;
2. получить кандидатов;
3. отсечь дубли;
4. выбрать top-N;
5. сгенерировать структурированный дайджест;
6. сохранить summary + ссылки + provenance;
7. отправить в Telegram;
8. сохранить в web history.

## 10. RAG pipeline
1. пользователь задает вопрос;
2. если это voice, делается STT;
3. query normalizer подготавливает запрос;
4. retrieval идет по chunks + metadata filters;
5. optional rerank;
6. LLM строит ответ;
7. в ответе отображаются ссылки на исходные посты.

## 11. Предлагаемая структура БД
### Core
- users
- topics
- channels
- user_topic_preferences
- user_channel_subscriptions
- digest_schedules

### Content
- posts
- post_metrics
- document_chunks
- embeddings

### Output
- digests
- digest_items

### Chat / Voice
- rag_conversations
- rag_messages
- audio_inputs
- transcripts

### Analytics
- events
- daily_metrics
- funnel_snapshots

### Ops
- ingestion_runs
- source_sync_logs
- digest_jobs

## 12. API boundary
### Web/API endpoints
- auth
- profile
- channels
- topics
- subscriptions
- digest history
- rag chat
- voice upload
- analytics dashboard

### Bot commands
- /start
- /link
- /settings
- /digest
- /topics
- /channels
- /help

## 13. Промпты и AI abstraction
Нужно выделить отдельный слой провайдеров:
- `LLMProvider`
- `EmbeddingProvider`
- `STTProvider`

И отдельный пакет промптов:
- digest_generation
- post_relevance_scoring
- topic_extraction
- rag_answering
- transcript_cleanup

Это позволит менять модель или Together endpoint без переписывания бизнес-логики.

## 14. Решение по моделям
Для MVP не нужен zoo из моделей.

Достаточно:
- 1 недорогая instruct/chat модель для summary, scoring и RAG answer generation;
- 1 embedding-модель;
- 1 STT provider.

Принцип выбора:
- низкая цена;
- стабильный API;
- хороший instruction-following;
- нормальный контекст;
- предсказуемая latency.

## 15. Решение по деплою
### Практичный вариант для дедлайна
- VPS / сервер
- код заливается по SSH
- backend + bot + scheduler + db крутятся на сервере
- systemd или supervisor для процессов
- Nginx как reverse proxy

### Когда Docker можно не брать
Если сроки жесткие и ты уверен, что справишься с ручным деплоем, Docker не обязателен.

Но нужен минимум дисциплины:
- `.env`
- requirements.txt / lockfile
- systemd unit files
- backup БД
- restart policy

### GitHub Pages
Подходит для:
- landing;
- статической витрины;
- документации.

Не подходит для:
- backend;
- server-side API;
- Telegram webhooks;
- runtime jobs.

## 16. Масштабирование после MVP
После MVP можно добавлять:
- self-serve import каналов;
- персонализацию;
- recommendations;
- рекламные подборки;
- B2B digest spaces;
- team/shared digests.
