# Hisob AI

SaaS для малого и среднего бизнеса Узбекистана: документы, учёт, склад, банковская сверка, AI-помощник.
Источник требований — [docs/MASTER_SPEC_Hisob_AI.md](docs/MASTER_SPEC_Hisob_AI.md). План, архитектура и решения — в [docs/](docs/).

## Статус

| Этап | Состояние |
|---|---|
| 0. Discovery (архитектура, ERD, ADR, дизайн, план) | готов, [docs/](docs/) |
| 1.1 Регистрация, организация, вход, аудит, изоляция организаций | **реализован**, отчёт: [docs/reports/stage-1.1.md](docs/reports/stage-1.1.md) |
| 1.2 Участники, приглашения по ссылке, роли | **реализован**, отчёт: [docs/reports/stage-1.2.md](docs/reports/stage-1.2.md) |
| 1.4 Три языка интерфейса (uz по умолчанию, ru, en) | **реализован**, отчёт: [docs/reports/stage-1.4.md](docs/reports/stage-1.4.md) |
| 1.3 Журнал аудита в UI, смена пароля, сессии | **реализован**, отчёт: [docs/reports/stage-1.3.md](docs/reports/stage-1.3.md). **Этап 1 завершён** |
| 2.1 Валюты организации и единицы измерения | **реализован**, отчёт: [docs/reports/stage-2.1.md](docs/reports/stage-2.1.md) |
| 2.2 Контрагенты + общий каркас списков | **реализован**, отчёт: [docs/reports/stage-2.2.md](docs/reports/stage-2.2.md) |
| 2.3 Товары и услуги (цена с валютой, штрихкоды) | **реализован**, отчёт: [docs/reports/stage-2.3.md](docs/reports/stage-2.3.md) |
| 2.4 Склады | **реализован**, отчёт: [docs/reports/stage-2.4.md](docs/reports/stage-2.4.md). **Этап 2 завершён** |
| 3+ Документы, документы, склад, учёт, банк, интеграции, AI | не начаты |

Что работает: регистрация и вход (cookie-сессии, Argon2id), создание организации (идемпотентно), переключение организаций, выход,
приглашение участников ссылкой, смена ролей и приостановка (с защитой последнего владельца), интерфейс на узбекском/русском/английском, валюты организации, единицы измерения, контрагенты (поиск, сортировка, пагинация, архив), товары и услуги (цена с валютой, штрихкоды), склады, просмотр журнала аудита, смена пароля, управление сессиями, изоляция данных между организациями (на уровне API и PostgreSQL RLS). Всё остальное в UI отсутствует намеренно.

## Запуск с нуля

Нужны Docker с Compose v2.

```bash
cp .env.example .env
# замените все change-me случайными значениями, например: openssl rand -hex 24
docker compose up -d --build
```

После старта: UI — http://127.0.0.1:3300, API — http://127.0.0.1:8300 (`/ready`, `/api/v1/docs`).
Порты слушают только 127.0.0.1; удалённый сервер открывайте через SSH-туннель:
`ssh -L 3300:localhost:3300 user@host`.

Миграции применяет одноразовый сервис `migrate` перед стартом API. Пароль БД меняется только вместе с томом `pgdata`
(роли создаются при первой инициализации, см. `infra/postgres/init/01-roles.sh`).

## Проверки

```bash
# Тесты бэкенда на настоящем PostgreSQL (создаётся БД hisob_test)
docker compose --profile test run --rm backend-tests
# E2E в Chromium (desktop + mobile); стек должен быть запущен
docker compose --profile test run --rm e2e

# Статические проверки (локально, нужны uv и Node 22+)
cd backend  && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports
cd frontend && npm ci && npm run lint && npm run typecheck && npm test
```

## Структура

```
backend/    FastAPI, SQLAlchemy 2, Alembic; app/platform (сквозное) + app/modules/{audit,identity,rbac,tenancy}
frontend/   Next.js (App Router), TypeScript
test/e2e/   Playwright
infra/      инициализация PostgreSQL (роли)
docs/       спецификация, архитектура, ERD, ADR, дизайн, план, отчёты, снимок OpenAPI
```

## Безопасность окружения

`.env` содержит пароли БД и не попадает в git. Приложение подключается ролью `hisob_app` (не владелец таблиц, без BYPASSRLS),
миграции — ролью `hisob_migrator`. Не используйте это окружение для реальных финансовых данных: бэкапы, мониторинг и
проверка требований к хранению данных (ТЗ §13) ещё не сделаны.
