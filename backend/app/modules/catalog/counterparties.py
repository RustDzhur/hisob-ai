"""Use cases: контрагенты."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.catalog.models import Counterparty
from app.modules.catalog.schemas import CounterpartyCreate, CounterpartyOut, CounterpartyUpdate
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

EDITABLE = ("name", "tin", "is_customer", "is_supplier", "address", "phone", "email", "notes")
SORTABLE: dict[str, SQLColumnExpression[Any]] = {
    "name": Counterparty.name,
    "tin": Counterparty.tin,
    "created_at": Counterparty.created_at,
}


def _out(c: Counterparty) -> CounterpartyOut:
    return CounterpartyOut(
        id=c.id,
        name=c.name,
        tin=c.tin,
        is_customer=c.is_customer,
        is_supplier=c.is_supplier,
        address=c.address,
        phone=c.phone,
        email=c.email,
        notes=c.notes,
        archived=c.archived_at is not None,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _snapshot(c: Counterparty) -> dict[str, Any]:
    d: dict[str, Any] = {f: getattr(c, f) for f in EDITABLE}
    d["archived"] = c.archived_at is not None
    return d


def _tin_taken(
    db: Session, org_id: uuid.UUID, tin: str, exclude: uuid.UUID | None
) -> AppError | None:
    other = db.scalar(
        select(Counterparty).where(Counterparty.organization_id == org_id, Counterparty.tin == tin)
    )
    if other is None or other.id == exclude:
        return None
    code = "counterparty_tin_taken_archived" if other.archived_at else "counterparty_tin_taken"
    return AppError(
        409,
        code,
        "Контрагент с таким ИНН/СТИР уже существует.",
        [
            {
                "field": "tin",
                "code": code,
                "message": "ИНН/СТИР уже используется.",
                "params": {"name": other.name},
            }
        ],
    )


def _get(db: Session, org_id: uuid.UUID, cid: uuid.UUID, *, lock: bool = False) -> Counterparty:
    stmt = select(Counterparty).where(
        Counterparty.id == cid, Counterparty.organization_id == org_id
    )
    c = db.scalar(stmt.with_for_update() if lock else stmt)
    if c is None:
        raise AppError(404, "not_found", "Контрагент не найден.")
    return c


def list_counterparties(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    role: str,
    status: str,
) -> Page[CounterpartyOut]:
    authorize(db, principal, org_id, rbac.CATALOG_READ)
    stmt = select(Counterparty).where(Counterparty.organization_id == org_id)
    if role == "customer":
        stmt = stmt.where(Counterparty.is_customer.is_(True))
    elif role == "supplier":
        stmt = stmt.where(Counterparty.is_supplier.is_(True))
    if status == "active":
        stmt = stmt.where(Counterparty.archived_at.is_(None))
    elif status == "archived":
        stmt = stmt.where(Counterparty.archived_at.is_not(None))
    stmt = search(
        stmt, lq.q, Counterparty.name, Counterparty.tin, Counterparty.email, Counterparty.phone
    )
    rows, total = paginate(
        db, stmt, lq, sort_map=SORTABLE, default_sort="name", id_column=Counterparty.id
    )
    return Page[CounterpartyOut](
        items=[_out(c) for c in rows], total=total, page=lq.page, page_size=lq.page_size
    )


def get_counterparty(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID
) -> CounterpartyOut:
    authorize(db, principal, org_id, rbac.CATALOG_READ)
    return _out(_get(db, org_id, cid))


def create_counterparty(
    db: Session, principal: Principal, org_id: uuid.UUID, data: CounterpartyCreate
) -> CounterpartyOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    if data.tin and (err := _tin_taken(db, org_id, data.tin, None)):
        raise err
    c = Counterparty(id=new_id(), organization_id=org_id, **data.model_dump())
    db.add(c)
    try:
        db.flush()
    except IntegrityError as exc:  # гонка: тот же ИНН создан параллельно
        db.rollback()
        authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
        raise (_tin_taken(db, org_id, data.tin or "", None) or _fallback_conflict()) from exc
    audit.record(
        db,
        action="counterparty.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="counterparty",
        object_id=c.id,
        after=_snapshot(c),
    )
    db.commit()
    return _out(c)


def _fallback_conflict() -> AppError:
    return AppError(409, "counterparty_tin_taken", "Контрагент с таким ИНН/СТИР уже существует.")


def update_counterparty(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    cid: uuid.UUID,
    data: CounterpartyUpdate,
) -> CounterpartyOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    c = _get(db, org_id, cid, lock=True)
    if c.archived_at is not None:
        raise AppError(409, "counterparty_archived", "Архивного контрагента нельзя изменять.")
    before = _snapshot(c)
    # Конфликт ИНН проверяем ДО изменения полей: иначе автосброс сессии даст 500 вместо 409.
    if "tin" in data.model_fields_set and data.tin and data.tin != c.tin:
        if err := _tin_taken(db, org_id, data.tin, c.id):
            raise err
    for field in data.model_fields_set & set(EDITABLE):
        value = getattr(data, field)
        if field == "name" and value is None:
            continue
        if field in ("is_customer", "is_supplier") and value is None:
            continue
        setattr(c, field, value)
    after = _snapshot(c)
    if after != before:
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise _fallback_conflict() from exc
        audit.record(
            db,
            action="counterparty.updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="counterparty",
            object_id=c.id,
            before=before,
            after=after,
        )
    db.commit()
    return _out(c)


def set_archived(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID, archived: bool
) -> CounterpartyOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    c = _get(db, org_id, cid, lock=True)
    if (c.archived_at is not None) == archived:
        return _out(c)  # уже в нужном состоянии — без записи в аудит
    c.archived_at = utcnow() if archived else None
    audit.record(
        db,
        action="counterparty.archived" if archived else "counterparty.restored",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="counterparty",
        object_id=c.id,
        after={"name": c.name, "tin": c.tin},
    )
    db.commit()
    return _out(c)
