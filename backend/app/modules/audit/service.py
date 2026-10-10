import uuid
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.platform import context
from app.platform.base import new_id, utcnow

# Ключи, значения которых никогда не попадают в журнал.
_FORBIDDEN_KEYS = {"password", "password_hash", "token", "token_hash", "secret"}


def _clean(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    leaked = _FORBIDDEN_KEYS & {k.lower() for k in data}
    if leaked:
        raise ValueError(f"секретные поля не допускаются в аудите: {sorted(leaked)}")
    return data


def record(
    db: Session,
    *,
    action: str,
    actor_user_id: uuid.UUID | None,
    organization_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Запись в той же транзакции, что и изменение. Коммит выполняет вызывающий."""
    db.execute(
        insert(AuditEvent).values(
            id=new_id(),
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before=_clean(before),
            after=_clean(after),
            correlation_id=context.correlation_id.get(),
            ip=context.client_ip.get(),
            occurred_at=utcnow(),
        )
    )
