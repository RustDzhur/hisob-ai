"""Тесты идут против настоящего PostgreSQL: отдельная БД hisob_test, миграции применяются
Alembic, приложение работает ролью hisob_app (RLS действует, как в проде)."""

import os
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

TEST_DB = "hisob_test"
_HOST = os.environ.get("TEST_POSTGRES_HOST", "postgres")
_ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "")

if _ADMIN_URL:
    os.environ["DATABASE_URL"] = (
        f"postgresql+psycopg://hisob_app:{os.environ['TEST_APP_PASSWORD']}@{_HOST}:5432/{TEST_DB}"
    )
    os.environ["MIGRATION_DATABASE_URL"] = (
        f"postgresql+psycopg://hisob_migrator:{os.environ['TEST_MIGRATOR_PASSWORD']}@{_HOST}:5432/{TEST_DB}"
    )
    os.environ["COOKIE_SECURE"] = "false"

TABLES = "audit_event, idempotency_key, invitation, membership, organization, sessions, login_attempt, users"


def _admin_engine(db: str = "postgres") -> Engine:
    url = re.sub(r"/[^/]+$", f"/{db}", _ADMIN_URL)
    return create_engine(url, isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def admin_engine() -> Iterator[Engine]:
    """Суперпользователь БД: создаёт тестовую БД, проверяет состояние в обход RLS."""
    if not _ADMIN_URL:
        pytest.skip(
            "нужна PostgreSQL: запускайте через docker compose --profile test run backend-tests"
        )
    root = _admin_engine("postgres")
    with root.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB} OWNER hisob_migrator"))
    root.dispose()

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")

    engine = _admin_engine(TEST_DB)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(admin_engine: Engine) -> Iterator[None]:
    with admin_engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    yield


@pytest.fixture
def app_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Подключение ролью приложения (hisob_app) — для проверок RLS на уровне SQL."""
    engine = create_engine(os.environ["DATABASE_URL"])
    yield engine
    engine.dispose()


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def client2(clean_db: None) -> Iterator[TestClient]:
    """Второй независимый «браузер» (свой cookie jar)."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def new_client(clean_db: None) -> Iterator[Callable[[], TestClient]]:
    """Фабрика независимых «браузеров» (у каждого свой cookie jar)."""
    from app.main import app

    stack = ExitStack()

    def make() -> TestClient:
        return stack.enter_context(TestClient(app))

    yield make
    stack.close()
