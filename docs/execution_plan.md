# AI Digest Assistant - Execution Plan

## 1. Цель
Собрать защищаемый MVP в ограниченное время так, чтобы:
- закрыть 12/12 требований;
- не утонуть в Telegram ingestion;
- оставить архитектуру расширяемой.

## 2. Принципы выполнения
1. Делаем только MVP.
2. Работаем короткими итерациями.
3. Каждый шаг заканчивается проверяемым результатом.
4. Codex получает маленькие, точные задачи.
5. Сначала закрываем пользовательский путь, потом улучшаем качество.

## 3. Приоритеты
### P0 - обязательно
- landing
- auth
- Telegram bot
- channel catalog
- subscriptions
- ingestion
- digest generation
- database

### P1 - нужно для сильной защиты
- history in web UI
- RAG assistant
- citations

### P2 - закрывает оставшиеся критерии и усиливает проект
- voice input + STT
- usage dashboard
- funnel analytics

### P3 - только если останется время
- folder import
- улучшенный ranking
- richer onboarding
- админка получше

## 4. Dependency graph
1. Scope docs
2. Repo skeleton
3. Database schema
4. Auth + basic web
5. Telegram bot link
6. Channel catalog
7. Ingestion
8. Digest scheduler + generator
9. Delivery
10. RAG
11. Voice
12. Analytics
13. Polish + deploy

## 5. Итерационный план

## Stage 0 - Project setup
### Outcome
Есть каркас проекта и документация.

### Tasks
- создать репозиторий;
- создать папки `web`, `api`, `bot`, `docs`;
- описать env-переменные;
- завести requirements / package.json;
- создать базовый README;
- положить `product_scope.md`, `architecture.md`, `execution_plan.md`, `Evolution.md`.

### Definition of done
- проект запускается локально;
- понятна структура;
- есть единая точка входа для следующего чата / агента.

## Stage 1 - Auth + landing + profile
### Outcome
Пользователь может открыть продукт и залогиниться.

### Tasks
- сделать landing;
- сделать auth flow;
- создать таблицу users;
- страницу профиля / onboarding.

### Definition of done
- есть рабочий login;
- пользователь сохраняется в БД.

## Stage 2 - Telegram bot base
### Outcome
Бот отвечает, пользователь может связать аккаунт.

### Tasks
- создать бота;
- команды `/start`, `/help`, `/link`;
- реализовать привязку telegram_id к user;
- отправка тестового сообщения.

### Definition of done
- бот отвечает;
- связка web-user <-> telegram account работает.

## Stage 3 - Channel catalog + subscriptions
### Outcome
Пользователь может выбрать каналы и темы.

### Tasks
- таблицы topics, channels, subscriptions;
- web UI выбора каналов;
- настройка периодичности;
- базовая админская загрузка каналов.

### Optional
- черновой folder import как способ массово выбрать каналы.

### Definition of done
- пользователь может сохранить свои источники и расписание.

## Stage 4 - Ingestion MVP
### Outcome
Система читает посты из выбранных каналов и кладет в БД.

### Tasks
- выбрать ingestion-механику;
- реализовать Telethon client;
- создать таблицы posts и post_metrics;
- нормализация текста;
- sync logs.

### Definition of done
- минимум 5-10 каналов стабильно синхронизируются;
- посты появляются в БД.

## Stage 5 - Digest generation MVP
### Outcome
По расписанию создается и отправляется дайджест.

### Tasks
- scheduler;
- selection of candidate posts;
- scoring;
- anti-dup logic;
- LLM summary generation;
- запись в tables digests, digest_items;
- отправка в Telegram.

### Definition of done
- пользователь получает осмысленный дайджест.

## Stage 6 - Web digest history
### Outcome
В web UI можно смотреть прошлые дайджесты.

### Tasks
- API для списка дайджестов;
- страница истории;
- карточка дайджеста со ссылками.

### Definition of done
- digest history доступна и читаема.

## Stage 7 - RAG assistant
### Outcome
Пользователь может задавать вопросы по собранному контенту.

### Tasks
- chunking;
- embeddings;
- pgvector retrieval;
- chat endpoint;
- UI чата;
- citations.

### Definition of done
- есть вопрос-ответ по источникам;
- ответ опирается на реальные документы.

## Stage 8 - Voice input
### Outcome
Можно отправить голосовой вопрос.

### Tasks
- обработка voice в Telegram или web;
- STT;
- передача текста в RAG pipeline;
- отображение transcript.

### Definition of done
- voice -> text -> answer работает.

## Stage 9 - Dashboard + funnel
### Outcome
Есть usage dashboard и funnel analytics.

### Tasks
- event schema;
- трекинг ключевых событий;
- агрегаты по дням;
- графики по usage;
- воронка активации.

### Suggested events
- landing_view
- signup_started
- signup_completed
- telegram_linked
- topics_selected
- channels_selected
- digest_schedule_created
- first_digest_generated
- first_digest_opened
- first_rag_query

### Definition of done
- можно показать преподавателю usage dashboard и funnel.

## Stage 10 - Deploy
### Outcome
Продукт доступен извне.

### Tasks
- подготовить сервер;
- настроить Python / Node окружение;
- поднять PostgreSQL;
- прокинуть env;
- настроить Nginx;
- настроить systemd;
- открыть webhook / HTTPS.

### Definition of done
- сервис доступен снаружи;
- бот и API переживают рестарт сервера.

## 6. Как работать с Codex
## Правило
Одна задача = один небольшой deliverable.

## Хороший формат промпта
- контекст модуля;
- конкретная задача;
- что нельзя ломать;
- критерии приемки;
- какие файлы менять.

## Примеры
- "Создай SQLAlchemy models для users, channels, subscriptions и Alembic migration"
- "Сделай FastAPI endpoint для сохранения пользовательских подписок"
- "Сделай aiogram handlers для /start и /link"
- "Реализуй Telethon ingestion service для публичных каналов"
- "Сделай Next.js страницу истории дайджестов"

## Антипаттерны
- "Сделай весь backend"
- "Сделай весь MVP"
- "Сделай production ready SaaS"

## 7. Риски и контрмеры
### Риск 1
Ingestion Telegram окажется сложнее, чем ожидалось.

**Контрмера:**
зафиксировать curated channels в MVP и не расширять scope.

### Риск 2
Слишком много времени уйдет на UI.

**Контрмера:**
сначала functional UI без красоты.

### Риск 3
Codex начнет генерировать несовместимые куски.

**Контрмера:**
маленькие задачи, строгие acceptance criteria, ручная проверка.

### Риск 4
RAG съест слишком много времени.

**Контрмера:**
сделать простой retrieval без сложного rerank на первом проходе.

### Риск 5
Deploy вручную будет нестабильным.

**Контрмера:**
systemd, Nginx, backup БД, отдельный deploy checklist.

## 8. Рекомендованный порядок на ближайшие дни
### Day 1
- документы;
- skeleton;
- БД;
- auth;
- landing.

### Day 2
- bot;
- link flow;
- channels;
- subscriptions.

### Day 3
- ingestion;
- sync logs;
- test data.

### Day 4
- digest generation;
- delivery;
- history.

### Day 5
- RAG.

### Day 6
- voice + analytics.

### Day 7
- deploy + polish + demo script.

## 9. Demo script для защиты
1. Открыть landing.
2. Показать auth.
3. Показать выбор каналов и тем.
4. Показать Telegram bot.
5. Показать уже сгенерированный digest.
6. Показать вопрос к RAG.
7. Показать голосовой запрос.
8. Показать dashboard и funnel.
9. Коротко объяснить архитектуру и почему выбрана именно такая.
