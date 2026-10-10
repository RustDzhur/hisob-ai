"""Минимальный запас и резервы (срез 4.3).

Резерв уменьшает ДОСТУПНОЕ количество, физический остаток не меняет. Создание резерва и любое проведение,
уменьшающее остаток, сериализуются блокировкой строки остатка, поэтому «двойного» резерва и списания
зарезервированного не бывает. Резерв освобождается вручную; списание зарезервированного количества
другими операциями блокируется, пока резерв действует.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.catalog.models import Product, UnitOfMeasure
from app.modules.identity.service import Principal
from app.modules.inventory.models import (
    StockBalance,
    StockMinLevel,
    StockReservation,
    Warehouse,
)
from app.modules.inventory.schemas import (
    MinLevelIn,
    NameRef,
    ReservationCreate,
    ReservationOut,
)
from app.modules.inventory.stock import _fail, _names, _unit_ref, qty_str, reserved_total
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

SORTABLE: dict[str, SQLColumnExpression[Any]] = {"created_at": StockReservation.created_at}


def _check_pair(
    db: Session, org_id: uuid.UUID, warehouse_id: uuid.UUID, product_id: uuid.UUID
) -> Product:
    wh = db.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.organization_id == org_id)
    )
    if wh is None:
        raise _fail(422, "warehouse_not_found", "Склад не найден.", "warehouse_id")
    if wh.archived_at is not None:
        raise _fail(422, "warehouse_archived", "Склад в архиве.", "warehouse_id")
    p = db.scalar(
        select(Product).where(Product.id == product_id, Product.organization_id == org_id)
    )
    if p is None:
        raise _fail(422, "product_not_found", "Товар не найден.", "product_id")
    if p.kind != "goods":
        raise _fail(
            422, "product_not_goods", "Услуга не участвует в складском учёте.", "product_id"
        )
    if p.archived_at is not None:
        raise _fail(422, "product_archived", "Товар в архиве.", "product_id")
    return p


def _ensure_balance(
    db: Session, org_id: uuid.UUID, warehouse_id: uuid.UUID, product_id: uuid.UUID
) -> None:
    db.execute(
        pg_insert(StockBalance)
        .values(
            organization_id=org_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            quantity=Decimal(0),
        )
        .on_conflict_do_nothing()
    )


# ---------------- минимальный запас ----------------


def set_min_level(db: Session, principal: Principal, org_id: uuid.UUID, data: MinLevelIn) -> None:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    _check_pair(db, org_id, data.warehouse_id, data.product_id)
    existing = db.scalar(
        select(StockMinLevel).where(
            StockMinLevel.organization_id == org_id,
            StockMinLevel.warehouse_id == data.warehouse_id,
            StockMinLevel.product_id == data.product_id,
        )
    )
    before = qty_str(existing.min_quantity) if existing else None
    if data.min_quantity is None:
        if existing is not None:
            db.execute(
                delete(StockMinLevel).where(
                    StockMinLevel.organization_id == org_id,
                    StockMinLevel.warehouse_id == data.warehouse_id,
                    StockMinLevel.product_id == data.product_id,
                )
            )
    else:
        _ensure_balance(
            db, org_id, data.warehouse_id, data.product_id
        )  # позиция видна в списке остатков
        if existing is None:
            db.add(
                StockMinLevel(
                    organization_id=org_id,
                    warehouse_id=data.warehouse_id,
                    product_id=data.product_id,
                    min_quantity=data.min_quantity,
                )
            )
        else:
            existing.min_quantity = data.min_quantity
            existing.updated_at = utcnow()
    after = qty_str(data.min_quantity) if data.min_quantity is not None else None
    if before != after:
        audit.record(
            db,
            action="stock.min_level_set",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="product",
            object_id=data.product_id,
            before={"min_quantity": before, "warehouse_id": str(data.warehouse_id)},
            after={"min_quantity": after, "warehouse_id": str(data.warehouse_id)},
        )
    db.commit()


# ---------------- резервы ----------------


def _out(
    db: Session, rows: list[tuple[StockReservation, Product, Warehouse, UnitOfMeasure]]
) -> list[ReservationOut]:
    names = _names(db, {r.created_by for r, *_ in rows})
    return [
        ReservationOut(
            id=r.id,
            warehouse=NameRef(id=w.id, name=str(w.name)),
            product_id=p.id,
            sku=str(p.sku),
            name=p.name,
            unit=_unit_ref(u),
            quantity=qty_str(r.quantity),
            reason=r.reason,
            status=r.status,
            created_by_name=names.get(r.created_by),
            created_at=r.created_at,
            released_at=r.released_at,
        )
        for r, p, w, u in rows
    ]


def _row(
    db: Session, org_id: uuid.UUID, rid: uuid.UUID
) -> list[tuple[StockReservation, Product, Warehouse, UnitOfMeasure]]:
    rows = db.execute(
        select(StockReservation, Product, Warehouse, UnitOfMeasure)
        .join(
            Product,
            (Product.id == StockReservation.product_id)
            & (Product.organization_id == StockReservation.organization_id),
        )
        .join(
            Warehouse,
            (Warehouse.id == StockReservation.warehouse_id)
            & (Warehouse.organization_id == StockReservation.organization_id),
        )
        .join(UnitOfMeasure, UnitOfMeasure.id == Product.unit_id)
        .where(StockReservation.id == rid, StockReservation.organization_id == org_id)
    ).all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def list_reservations(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    status: str,
    warehouse_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
) -> Page[ReservationOut]:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    stmt: Any = (
        select(StockReservation)
        .join(
            Product,
            (Product.id == StockReservation.product_id)
            & (Product.organization_id == StockReservation.organization_id),
        )
        .where(StockReservation.organization_id == org_id)
    )
    if status in ("ACTIVE", "RELEASED"):
        stmt = stmt.where(StockReservation.status == status)
    if warehouse_id:
        stmt = stmt.where(StockReservation.warehouse_id == warehouse_id)
    if product_id:
        stmt = stmt.where(StockReservation.product_id == product_id)
    stmt = search(stmt, lq.q, Product.sku, Product.name, StockReservation.reason)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map=SORTABLE,
        default_sort="created_at",
        id_column=StockReservation.id,
        default_order="desc",
    )
    items: list[ReservationOut] = []
    for r in rows:
        items.extend(_out(db, _row(db, org_id, r.id)))
    return Page[ReservationOut](items=items, total=total, page=lq.page, page_size=lq.page_size)


def create_reservation(
    db: Session, principal: Principal, org_id: uuid.UUID, data: ReservationCreate
) -> ReservationOut:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    _check_pair(db, org_id, data.warehouse_id, data.product_id)
    _ensure_balance(db, org_id, data.warehouse_id, data.product_id)
    bal = db.scalar(
        select(StockBalance)
        .where(
            StockBalance.organization_id == org_id,
            StockBalance.warehouse_id == data.warehouse_id,
            StockBalance.product_id == data.product_id,
        )
        .with_for_update()  # та же блокировка, что при проведении: резерв и списание не разъедутся
    )
    assert bal is not None
    reserved = reserved_total(db, org_id, data.warehouse_id, data.product_id)
    available = bal.quantity - reserved
    if data.quantity > available:
        db.rollback()
        raise AppError(
            409,
            "insufficient_available",
            "Недостаточно свободного остатка для резерва.",
            [
                {
                    "field": "quantity",
                    "code": "insufficient_available",
                    "message": "Недостаточно свободного остатка.",
                    "params": {
                        "available": qty_str(max(available, Decimal(0))),
                        "required": qty_str(data.quantity),
                    },
                }
            ],
        )
    r = StockReservation(
        id=new_id(),
        organization_id=org_id,
        warehouse_id=data.warehouse_id,
        product_id=data.product_id,
        quantity=data.quantity,
        reason=data.reason,
        created_by=principal.user.id,
    )
    db.add(r)
    audit.record(
        db,
        action="stock_reservation.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_reservation",
        object_id=r.id,
        after={
            "warehouse_id": str(data.warehouse_id),
            "product_id": str(data.product_id),
            "quantity": qty_str(data.quantity),
        },
    )
    db.commit()
    return _out(db, _row(db, org_id, r.id))[0]


def release_reservation(
    db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID
) -> ReservationOut:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    r = db.scalar(
        select(StockReservation)
        .where(StockReservation.id == rid, StockReservation.organization_id == org_id)
        .with_for_update()
    )
    if r is None:
        raise AppError(404, "not_found", "Резерв не найден.")
    if r.status != "ACTIVE":
        raise AppError(409, "reservation_not_active", "Резерв уже снят.")
    r.status = "RELEASED"
    r.released_by = principal.user.id
    r.released_at = utcnow()
    audit.record(
        db,
        action="stock_reservation.released",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_reservation",
        object_id=r.id,
        after={"quantity": qty_str(r.quantity)},
    )
    db.commit()
    return _out(db, _row(db, org_id, r.id))[0]
