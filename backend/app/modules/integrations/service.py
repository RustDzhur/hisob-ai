"""Use cases: интеграции (этап 7). Реальных адаптеров нет; работает только тестовый (MOCK)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.integrations.adapters import ADAPTERS, PROVIDERS, AdapterError
from app.modules.integrations.models import IntegrationConnection, IntegrationSyncRun
from app.modules.integrations.schemas import ProviderOut, SyncRunOut
from app.modules.tenancy.access import authorize
from app.platform.base import utcnow
from app.platform.errors import AppError

READ = "integrations.read"
MANAGE = "integrations.manage"


def _fail(http: int, code: str, message: str) -> AppError:
    return AppError(http, code, message)


def _provider(code: str) -> Any:
    info = PROVIDERS.get(code)
    if info is None:
        raise _fail(404, "integration_unknown", "Такой интеграции нет.")
    return info


def _connection(
    db: Session, org_id: uuid.UUID, code: str, *, lock: bool = False
) -> IntegrationConnection | None:
    stmt = select(IntegrationConnection).where(
        IntegrationConnection.organization_id == org_id, IntegrationConnection.provider == code
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def list_providers(db: Session, principal: Principal, org_id: uuid.UUID) -> list[ProviderOut]:
    authorize(db, principal, org_id, READ)
    conns = {
        c.provider: c
        for c in db.scalars(
            select(IntegrationConnection).where(IntegrationConnection.organization_id == org_id)
        )
    }
    out = []
    for info in PROVIDERS.values():
        c = conns.get(info.code)
        out.append(
            ProviderOut(
                code=info.code,
                title=info.title,
                kind=info.kind,
                status=info.status,
                docs_received=info.docs_received,
                capabilities=list(info.capabilities),
                enabled=bool(c and c.enabled),
                can_sync=info.code in ADAPTERS,
                last_sync_at=c.last_sync_at if c else None,
                sync_cursor=c.sync_cursor if c else None,
            )
        )
    return out


def set_enabled(
    db: Session, principal: Principal, org_id: uuid.UUID, code: str, enabled: bool
) -> ProviderOut:
    authorize(db, principal, org_id, MANAGE)
    info = _provider(code)
    if code not in ADAPTERS:
        raise _fail(
            409,
            "integration_not_configured",
            "Интеграция не настроена: официальная документация и доступы не получены.",
        )
    c = _connection(db, org_id, code, lock=True)
    if c is None:
        c = IntegrationConnection(
            organization_id=org_id,
            provider=info.code,
            enabled=enabled,
            created_by=principal.user.id,
        )
        db.add(c)
        try:
            db.flush()
        except IntegrityError:  # параллельное включение
            db.rollback()
            c = _connection(db, org_id, code, lock=True)
            assert c is not None
            c.enabled = enabled
    elif c.enabled != enabled:
        c.enabled = enabled
        c.updated_at = utcnow()
    audit.record(
        db,
        action="integration.enabled" if enabled else "integration.disabled",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="integration",
        object_id=c.id,
        after={"provider": code, "mode": info.status},
    )
    db.commit()
    return next(p for p in list_providers(db, principal, org_id) if p.code == code)


def _run_out(db: Session, rows: list[IntegrationSyncRun]) -> list[SyncRunOut]:
    names = {
        u.id: u.full_name
        for u in db.scalars(select(User).where(User.id.in_({r.created_by for r in rows})))
    }
    return [
        SyncRunOut(
            id=r.id,
            provider=r.provider,
            mode=r.mode,
            request_id=r.request_id,
            status=r.status,
            cursor_before=r.cursor_before,
            cursor_after=r.cursor_after,
            records_count=r.records_count,
            error_code=r.error_code,
            created_by_name=names.get(r.created_by),
            created_at=r.created_at,
        )
        for r in rows
    ]


def sync(
    db: Session, principal: Principal, org_id: uuid.UUID, code: str, request_id: uuid.UUID
) -> SyncRunOut:
    """Один запуск синхронизации. Повтор с тем же request_id возвращает тот же запуск и курсор не двигает."""
    authorize(db, principal, org_id, MANAGE)
    info = _provider(code)
    adapter = ADAPTERS.get(code)
    if adapter is None:
        raise _fail(
            409,
            "integration_not_configured",
            "Интеграция не настроена: официальная документация и доступы не получены.",
        )
    conn = _connection(db, org_id, code, lock=True)  # сериализует параллельные запуски
    if conn is None or not conn.enabled:
        raise _fail(409, "integration_disabled", "Интеграция не включена.")
    prev = db.scalars(
        select(IntegrationSyncRun).where(
            IntegrationSyncRun.organization_id == org_id,
            IntegrationSyncRun.provider == code,
            IntegrationSyncRun.request_id == request_id,
        )
    ).first()
    if prev is not None:
        db.rollback()
        return _run_out(db, [prev])[0]
    run = IntegrationSyncRun(
        organization_id=org_id,
        provider=code,
        mode="MOCK" if info.status == "MOCK" else "REAL",
        request_id=request_id,
        cursor_before=conn.sync_cursor,
        created_by=principal.user.id,
    )
    try:
        res = adapter.sync(conn.sync_cursor)
        run.status, run.records_count, run.cursor_after = "SUCCEEDED", res.records, res.next_cursor
        conn.sync_cursor = res.next_cursor
        conn.last_sync_at = utcnow()
    except AdapterError as exc:
        run.status, run.error_code, run.cursor_after = "FAILED", exc.code, conn.sync_cursor
    db.add(run)
    audit.record(
        db,
        action="integration.sync",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="integration",
        object_id=conn.id,
        after={"provider": code, "status": run.status, "records": run.records_count},
    )
    db.commit()
    return _run_out(db, [run])[0]


def list_runs(
    db: Session, principal: Principal, org_id: uuid.UUID, provider: str | None, limit: int = 50
) -> list[SyncRunOut]:
    authorize(db, principal, org_id, READ)
    stmt = select(IntegrationSyncRun).where(IntegrationSyncRun.organization_id == org_id)
    if provider:
        stmt = stmt.where(IntegrationSyncRun.provider == provider)
    rows = list(
        db.scalars(
            stmt.order_by(IntegrationSyncRun.created_at.desc(), IntegrationSyncRun.id.desc()).limit(
                limit
            )
        )
    )
    return _run_out(db, rows)
