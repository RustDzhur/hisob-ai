"""Чтение журнала аудита организации (право audit.read). Запись событий — модуль audit."""

import uuid
import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.schemas import AuditActor, AuditEventOut, AuditPage
from app.platform.errors import AppError

MAX_PAGE_SIZE = 100


def _filters(
    org_id: uuid.UUID,
    tz: zoneinfo.ZoneInfo,
    action: str | None,
    actor_user_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    conds: list[Any] = [AuditEvent.organization_id == org_id]
    if action:
        conds.append(AuditEvent.action == action)
    if actor_user_id:
        conds.append(AuditEvent.actor_user_id == actor_user_id)
    if date_from:  # границы дня — в часовом поясе организации
        conds.append(AuditEvent.occurred_at >= datetime.combine(date_from, time.min, tzinfo=tz))
    if date_to:
        conds.append(
            AuditEvent.occurred_at
            < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
        )
    return conds


def list_events(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    page: int,
    page_size: int,
    action: str | None,
    actor_user_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> AuditPage:
    access = authorize(db, principal, org_id, rbac.AUDIT_READ)
    if date_from and date_to and date_from > date_to:
        raise AppError(
            422,
            "validation_error",
            "Дата «с» позже даты «по».",
            [{"field": "date_from", "message": "Дата «с» позже даты «по»."}],
        )
    tz = zoneinfo.ZoneInfo(access.org.timezone)
    conds = _filters(org_id, tz, action, actor_user_id, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conds)) or 0
    rows = db.execute(
        select(AuditEvent, User)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(*conds)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    items = [
        AuditEventOut(
            id=e.id,
            occurred_at=e.occurred_at,
            action=e.action,
            actor=AuditActor(id=u.id, email=u.email, full_name=u.full_name) if u else None,
            object_type=e.object_type,
            object_id=e.object_id,
            before=e.before,
            after=e.after,
            correlation_id=e.correlation_id,
            ip=e.ip,
        )
        for e, u in rows
    ]
    return AuditPage(items=items, total=total, page=page, page_size=page_size)


def list_actions(db: Session, principal: Principal, org_id: uuid.UUID) -> list[str]:
    authorize(db, principal, org_id, rbac.AUDIT_READ)
    return list(
        db.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.organization_id == org_id)
            .distinct()
            .order_by(AuditEvent.action)
        )
    )
