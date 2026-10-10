# Hisob AI — Архитектура (Этап 0)

Статус: **ЧЕРНОВИК НА ПРОВЕРКУ**. Источник требований: `docs/MASTER_SPEC_Hisob_AI.md` (далее «ТЗ»). Кода в репозитории пока нет.

## 1. Состояние репозитория на старте

| Что | Факт |
|---|---|
| Коммитов | 0 |
| В индексе git | `README.md` и `docker-compose.yml` — **оба пустые (0 байт)** |
| Пустые каталоги | `backend/`, `frontend/`, `test/` |
| Исходники | отсутствуют |
| Окружение | Node 24.18, Python 3.13.7, `uv` и `brew` есть; **нет Docker, psql, redis, pnpm** |

Следствие: PostgreSQL для тестов нужен до начала этапа 1 (см. открытый вопрос Q1).

## 2. Стиль: модульный монолит

Один деплоймент backend + один frontend + воркер той же кодовой базы. Модули общаются **только через публичные application-сервисы** друг друга (не через чужие таблицы/ORM-модели). Это проверяется автоматически (import-linter, см. ADR-004).

```
                        ┌────────────────────────────────────────────┐
 Браузер / Mobile web   │  Next.js (React, TS) — только UI,          │
 ──────────────────────▶│  никаких бизнес-правил, ходит в /api/v1    │
                        └───────────────┬────────────────────────────┘
                                        │ HTTPS, cookie-сессия + CSRF
                        ┌───────────────▼────────────────────────────┐
                        │  FastAPI  (routers = тонкий слой)          │
                        │  middleware: correlation-id, auth, tenant, │
                        │  rate-limit, единый формат ошибок          │
                        ├────────────────────────────────────────────┤
                        │  Application services (use cases)          │
                        │  ─ проверка прав (RBAC) ─ транзакция ─     │
                        │  ─ идемпотентность ─ запись audit_event ─  │
                        ├────────────────────────────────────────────┤
                        │  Domain (чистый Python: правила, Decimal,  │
                        │  state machines, без I/O)                  │
                        ├────────────────────────────────────────────┤
                        │  Persistence (SQLAlchemy 2, репозитории,   │
                        │  Alembic, RLS по organization_id)          │
                        └──────┬──────────────────┬──────────────────┘
                               │                  │
                      ┌────────▼───────┐   ┌──────▼───────┐
                      │ PostgreSQL     │   │ Redis        │ очередь, rate-limit, кэш
                      │ источник истины│   │ НЕ источник  │ (этап 3+)
                      └────────────────┘   │ финансов     │
                                           └──────┬───────┘
                                                  │
                                         ┌────────▼────────┐   ┌───────────────┐
                                         │ Worker (Dramatiq)│──▶│ AI Gateway    │ (этап 3+)
                                         │ OCR/AI/импорт    │   │ адаптеры LLM/ │
                                         └────────┬────────┘   │ OCR провайдеров│
                                                  │            └───────────────┘
                                         ┌────────▼────────┐
                                         │ S3-совместимое  │ файлы (этап 3+)
                                         └─────────────────┘
```

Поток обычной операции (ТЗ §6): `UI → API/Auth → application service → domain validation → DB transaction → audit event → ответ`. AI не имеет прямого доступа к БД; он получает данные только через типизированные read-only инструменты (этап 8).

## 3. Модули (bounded contexts)

| Модуль | Ответственность | Этап | Зависит от |
|---|---|---|---|
| `platform` | конфиг, БД-сессия, транзакции, ошибки, идемпотентность, correlation-id, время/Decimal-утилиты | 1 | — |
| `identity` | пользователи, пароли, сессии, (MFA позже) | 1 | platform |
| `tenancy` | организации, членство, приглашения, текущая организация запроса | 1 | identity |
| `rbac` | роли, права, проверка `require(permission)` | 1 | tenancy |
| `audit` | append-only журнал событий, чтение журнала | 1 | platform |
| `catalog` | контрагенты, товары, единицы, штрихкоды/идентификаторы | 2 | tenancy, audit |
| `inventory` | склады, движения, остатки, резервы, инвентаризация | 2 (склады) / 4 | catalog |
| `documents` | файлы, hash/dedup, состояния, поля, review | 3 | catalog, audit |
| `ai_gateway` | единый адаптер LLM/OCR, схемы вывода, версии шаблонов | 3 | documents |
| `accounting` | периоды, план счетов, проводки, правила, сторно | 5 | inventory, documents |
| `banking` | выписки, транзакции, сверка | 6 | accounting, catalog |
| `reporting` | объяснимые отчёты (только чтение) | 6 | все учётные |
| `integrations` | контракт адаптера, журнал синхронизации; адаптеры Multibank/Faktura.uz/Didox/Generic | 7 | documents, banking |
| `copilot` | чат, read-only tools, источники | 8 | reporting + read-сервисы |

Правило зависимостей: стрелки только «вниз» по таблице; циклы запрещены. Межмодульные события (например «документ утверждён → предложить движения») — через явные application-вызовы либо outbox-таблицу в БД (ADR-009), а не через прямые ORM-связи.

### Структура внутри модуля

> Фактически в срезе 1.1 модули плоские (`models.py`, `service.py`, `router.py`, `schemas.py`); разделение на `domain/` и `infrastructure/` вводится, когда появляется доменная логика. Порядок слоёв (import-linter): `tenancy → rbac → identity → audit → platform`; `rbac` — глобальные роли/права, проверка членства живёт в `tenancy`.
```
modules/<name>/
  api/            # FastAPI router + Pydantic схемы запрос/ответ (ORM наружу не отдаём)
  application/    # use cases: права → транзакция → домен → аудит
  domain/         # сущности, value objects, правила, state machines (без I/O)
  infrastructure/ # SQLAlchemy модели и репозитории, внешние клиенты
  tests/
```

## 4. Сквозные механизмы (обязательны с этапа 1)

1. **Tenant isolation, два слоя**
   - Слой приложения: репозитории тенантных таблиц принимают `OrgContext` и всегда добавляют `organization_id`; обращение без контекста — ошибка на уровне типов/тестов.
   - Слой БД (defense in depth): PostgreSQL Row-Level Security; на транзакцию выполняется `SET LOCAL app.current_org = '<uuid>'`; приложение подключается ролью без `BYPASSRLS`. Миграции идут отдельной ролью. (ADR-005)
   - Доказательство: серверные тесты «пользователь организации A не читает/не пишет данные B» для каждого тенантного ресурса + тест, что запрос без контекста организации не видит строк.
2. **RBAC**: права — константы в коде (`documents.approve`, `members.manage` …), роли — наборы прав в БД (системные роли неизменяемы на этапе 1). Каждый use case начинается с `require(ctx, permission)`. Отказ → 403 + audit-событие `access.denied` для чувствительных действий.
3. **Аудит**: `audit_event` — append-only (триггер БД запрещает UPDATE/DELETE), пишется в **той же транзакции**, что и изменение. Поля: кто, организация, действие, объект, до/после (без секретов), correlation_id, IP, время.
4. **Деньги и количества**: `Decimal` в Python, `NUMERIC` в PostgreSQL, валюта — явное поле (`CHAR(3)`), float запрещён (линтер-правило + тест). Правила округления — **не определены ТЗ** → открытый вопрос Q6; до ответа денежной арифметики нет, кроме хранения.
5. **Идемпотентность**: заголовок `Idempotency-Key` на все POST, создающие/проводящие учётные записи; таблица `idempotency_key (organization_id, key, request_hash, response, status)` с уникальностью; повтор с тем же ключом возвращает сохранённый результат, с другим телом — 422.
6. **Единый формат ошибок** (RFC 9457-подобный): `{type, code, message, details[], correlation_id}`. Коды стабильны и документируются.
7. **Время**: хранение `TIMESTAMPTZ` в UTC; бизнес-даты (дата документа, проводки) — `DATE` в часовом поясе организации (по умолчанию `Asia/Tashkent`).
8. **Неизменяемость учёта**: проведённые записи не UPDATE/DELETE — на уровне БД триггерами (этапы 4–5); исправления — сторно/корректировки.
9. **Секреты**: только env/secret manager; секреты интеграций шифруются (envelope, ключ вне БД), клиенту не возвращаются.
10. **Наблюдаемость**: структурные JSON-логи с correlation_id, `/health` (liveness) и `/ready` (БД), метрики — этап 9.

## 5. Аутентификация (этап 1)

Серверные сессии: opaque-токен (256 бит случайных), в БД хранится только его SHA-256; cookie `HttpOnly; Secure; SameSite=Lax`, CSRF-токен (double-submit) на мутирующие запросы. Пароли — Argon2id. Блокировка/троттлинг входа по IP+email. Сессия отзывается выходом и сменой пароля. MFA — «поэтапно» (ТЗ §13), в этап 1 **не входит**, но модель допускает (ADR-003).

**Не входит в этап 1 без решения владельца**: отправка писем (сброс пароля, приглашения) — провайдер почты не выбран (Q3). Приглашение в этапе 1 создаёт одноразовую ссылку, которую админ копирует вручную (честно подписано в UI); письма не имитируются.

## 6. Деплой и окружения

- `dev`: Docker Compose — `postgres:16`, `redis:7`, `storage` (SeaweedFS, S3 API; с этапа 3), `backend`, `worker`, `frontend`. Без Docker возможен локальный запуск (Homebrew Postgres) — см. Q1.
- `ci`: GitHub Actions (предположение — Q2): lint → typecheck → unit → integration (service-контейнер Postgres) → Playwright → dependency/security scan.
- `staging`/`prod`: этап 9, площадка не выбрана (Q4); данные Узбекистана и трансграничная передача требуют юридической проверки (ТЗ §13).

## 7. Дерево репозитория (целевое)

```
hisob-ai/
├── README.md                    # запуск с нуля, команды, статус этапов
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
├── docs/
│   ├── MASTER_SPEC_Hisob_AI.md  # источник требований
│   ├── 01-architecture.md
│   ├── 02-erd.md
│   ├── 03-adr.md
│   ├── 04-design.md
│   ├── 05-roadmap.md            # этапы, критерии приёмки, вопросы, риски
│   ├── api/                     # генерируемый OpenAPI snapshot (с этапа 1)
│   └── reports/stage-N.md       # отчёты по этапам (факты тестов)
├── backend/
│   ├── pyproject.toml           # uv, ruff, mypy --strict, pytest, import-linter
│   ├── alembic.ini
│   ├── migrations/versions/
│   ├── app/
│   │   ├── main.py
│   │   ├── platform/            # config, db, errors, idempotency, logging, money
│   │   └── modules/
│   │       ├── identity/  tenancy/  rbac/  audit/        # этап 1
│   │       └── catalog/ inventory/ documents/ ...        # позже, пусто не создаём
│   └── tests/
│       ├── unit/  integration/  security/  invariants/
├── frontend/
│   ├── package.json  tsconfig.json  next.config.ts
│   └── src/
│       ├── app/                 # App Router: (auth)/ и (app)/
│       ├── components/ui/       # дизайн-система (токены → компоненты)
│       ├── components/<feature>/
│       ├── lib/api/             # типизированный клиент из OpenAPI
│       └── i18n/                # ru (основной), uz — Q7
├── test/
│   └── e2e/                     # Playwright (использует уже существующий каталог test/)
└── infra/
    └── postgres/init/           # роли: migrator, app (без BYPASSRLS)
```
Каталоги будущих модулей создаются **только когда появляется код**, чтобы не плодить пустые заглушки.

## 8. Технологические решения (кратко, детали в ADR)

Python 3.12+ (локально 3.13), FastAPI, Pydantic 2, SQLAlchemy 2 (sync-сессии в этапе 1 — проще и надёжнее для транзакций; ADR-006), Alembic, PostgreSQL 16, Dramatiq+Redis, Argon2id, `uv`, ruff, mypy strict, pytest (+Hypothesis для инвариантов). Frontend: Next.js (App Router), TypeScript strict, Tailwind + токены, Radix-примитивы, TanStack Query, Zod, Vitest, Playwright, ESLint.
