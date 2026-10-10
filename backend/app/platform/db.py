import uuid
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.platform.config import get_settings

_CTX_KEY = "tenant_ctx"
_SET_CTX_SQL = (
    "SELECT set_config('app.current_user', %s, true), set_config('app.current_org', %s, true)"
)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def _ctx(session: Session) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    value = session.info.get(_CTX_KEY)
    if value is None:
        return (None, None)
    user_id, org_id = value
    return (user_id, org_id)


@event.listens_for(Session, "after_begin")
def _apply_context_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    """Каждая транзакция начинается с актуального контекста RLS.

    set_config(..., true) действует только до конца транзакции, поэтому контекст
    не может «протечь» через пул соединений в другой запрос.
    """
    user_id, org_id = _ctx(session)
    connection.exec_driver_sql(
        _SET_CTX_SQL, (str(user_id) if user_id else "", str(org_id) if org_id else "")
    )


def set_context(
    db: Session, *, user_id: uuid.UUID | None = None, org_id: uuid.UUID | None = None
) -> None:
    """Задаёт пользователя/организацию для RLS в текущей и следующих транзакциях сессии."""
    cur_user, cur_org = _ctx(db)
    new_user = user_id if user_id is not None else cur_user
    new_org = org_id if org_id is not None else cur_org
    db.info[_CTX_KEY] = (new_user, new_org)
    db.execute(
        text(
            "SELECT set_config('app.current_user', :u, true), set_config('app.current_org', :o, true)"
        ),
        {"u": str(new_user) if new_user else "", "o": str(new_org) if new_org else ""},
    )


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость. Коммит выполняют application-сервисы (границы транзакции)."""
    with get_sessionmaker()() as session:
        yield session
