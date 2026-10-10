"""Use cases: склады."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.identity.service import Principal
from app.modules.inventory.models import Warehouse
from app.modules.inventory.schemas import WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

EDITABLE = ("name", "address", "notes")
SORTABLE: dict[str, SQLColumnExpression[Any]] = {
    "name": Warehouse.name,
    "created_at": Warehouse.created_at,
}


def _out(w: Warehouse) -> WarehouseOut:
    return WarehouseOut(
        id=w.id,
        name=str(w.name),
        address=w.address,
        notes=w.notes,
        archived=w.archived_at is not None,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


def _snapshot(w: Warehouse) -> dict[str, Any]:
    return {
        "name": str(w.name),
        "address": w.address,
        "notes": w.notes,
        "archived": w.archived_at is not None,
    }


def _name_taken(
    db: Session, org_id: uuid.UUID, name: str, exclude: uuid.UUID | None
) -> AppError | None:
    other = db.scalar(
        select(Warehouse).where(Warehouse.organization_id == org_id, Warehouse.name == name)
    )
    if other is None or other.id == exclude:
        return None
    code = "warehouse_name_taken_archived" if other.archived_at else "warehouse_name_taken"
    return AppError(
        409,
        code,
        "Склад с таким названием уже существует.",
        [{"field": "name", "code": code, "message": "Название уже используется."}],
    )


def _get(db: Session, org_id: uuid.UUID, wid: uuid.UUID, *, lock: bool = False) -> Warehouse:
    stmt = select(Warehouse).where(Warehouse.id == wid, Warehouse.organization_id == org_id)
    w = db.scalar(stmt.with_for_update() if lock else stmt)
    if w is None:
        raise AppError(404, "not_found", "Склад не найден.")
    return w


def list_warehouses(
    db: Session, principal: Principal, org_id: uuid.UUID, lq: ListQuery, *, status: str
) -> Page[WarehouseOut]:
    authorize(db, principal, org_id, rbac.WAREHOUSES_READ)
    stmt = select(Warehouse).where(Warehouse.organization_id == org_id)
    if status == "active":
        stmt = stmt.where(Warehouse.archived_at.is_(None))
    elif status == "archived":
        stmt = stmt.where(Warehouse.archived_at.is_not(None))
    stmt = search(stmt, lq.q, Warehouse.name, Warehouse.address)
    rows, total = paginate(
        db, stmt, lq, sort_map=SORTABLE, default_sort="name", id_column=Warehouse.id
    )
    return Page[WarehouseOut](
        items=[_out(w) for w in rows], total=total, page=lq.page, page_size=lq.page_size
    )


def get_warehouse(
    db: Session, principal: Principal, org_id: uuid.UUID, wid: uuid.UUID
) -> WarehouseOut:
    authorize(db, principal, org_id, rbac.WAREHOUSES_READ)
    return _out(_get(db, org_id, wid))


def create_warehouse(
    db: Session, principal: Principal, org_id: uuid.UUID, data: WarehouseCreate
) -> WarehouseOut:
    authorize(db, principal, org_id, rbac.WAREHOUSES_MANAGE)
    if err := _name_taken(db, org_id, data.name, None):
        raise err
    w = Warehouse(id=new_id(), organization_id=org_id, **data.model_dump())
    db.add(w)
    try:
        db.flush()
    except IntegrityError as exc:  # гонка: то же название создано параллельно
        db.rollback()
        raise AppError(
            409, "warehouse_name_taken", "Склад с таким названием уже существует."
        ) from exc
    audit.record(
        db,
        action="warehouse.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="warehouse",
        object_id=w.id,
        after=_snapshot(w),
    )
    db.commit()
    return _out(w)


def update_warehouse(
    db: Session, principal: Principal, org_id: uuid.UUID, wid: uuid.UUID, data: WarehouseUpdate
) -> WarehouseOut:
    authorize(db, principal, org_id, rbac.WAREHOUSES_MANAGE)
    w = _get(db, org_id, wid, lock=True)
    if w.archived_at is not None:
        raise AppError(409, "warehouse_archived", "Архивный склад нельзя изменять.")
    before = _snapshot(w)
    # конфликт названия проверяем ДО изменения полей (автосброс сессии иначе даст 500)
    if "name" in data.model_fields_set and data.name and data.name.lower() != str(w.name).lower():
        if err := _name_taken(db, org_id, data.name, w.id):
            raise err
    for field in data.model_fields_set & set(EDITABLE):
        value = getattr(data, field)
        if field == "name" and value is None:
            continue
        setattr(w, field, value)
    after = _snapshot(w)
    if after != before:
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise AppError(
                409, "warehouse_name_taken", "Склад с таким названием уже существует."
            ) from exc
        audit.record(
            db,
            action="warehouse.updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="warehouse",
            object_id=w.id,
            before=before,
            after=after,
        )
    db.commit()
    return _out(w)


def set_archived(
    db: Session, principal: Principal, org_id: uuid.UUID, wid: uuid.UUID, archived: bool
) -> WarehouseOut:
    authorize(db, principal, org_id, rbac.WAREHOUSES_MANAGE)
    w = _get(db, org_id, wid, lock=True)
    if (w.archived_at is not None) == archived:
        return _out(w)
    w.archived_at = utcnow() if archived else None
    audit.record(
        db,
        action="warehouse.archived" if archived else "warehouse.restored",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="warehouse",
        object_id=w.id,
        after={"name": str(w.name)},
    )
    db.commit()
    return _out(w)
