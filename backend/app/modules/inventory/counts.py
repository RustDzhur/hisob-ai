"""Инвентаризация (срез 4.3): пересчёт склада и проведение расхождений корректирующей операцией.

- Начало: строки заполняются товарами с ненулевым остатком склада, «ожидаемое» — снимок остатка.
- Пересчитанное количество вводится построчно; непересчитанные строки (пусто) не корректируются.
- Проведение: остатки блокируются, расхождение считается от ТЕКУЩЕГО остатка под блокировкой
  (движения во время пересчёта не теряются), создаётся проведённая операция `count_adjustment`
  с движениями — остаток не «перезаписывается», а меняется движениями.
"""

import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.catalog.models import Product, UnitOfMeasure
from app.modules.identity.service import Principal
from app.modules.inventory.models import (
    StockBalance,
    StockCount,
    StockCountLine,
    StockOperation,
    StockOperationLine,
    Warehouse,
)
from app.modules.inventory.schemas import (
    CountCreate,
    CountLineIn,
    CountLineOut,
    CountListItem,
    CountOut,
    CountUpdate,
    NameRef,
)
from app.modules.inventory.stock import (
    _apply_posting,
    _fail,
    _names,
    _unit_ref,
    qty_str,
)
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

MAX_LINES = 2000


def _conflict() -> AppError:
    return AppError(
        409, "count_conflict", "Инвентаризацию изменил другой пользователь. Обновите страницу."
    )


def _load(db: Session, org_id: uuid.UUID, cid: uuid.UUID, *, lock: bool = False) -> StockCount:
    stmt = select(StockCount).where(StockCount.id == cid, StockCount.organization_id == org_id)
    c = db.scalar(stmt.with_for_update() if lock else stmt)
    if c is None:
        raise AppError(404, "not_found", "Инвентаризация не найдена.")
    return c


def _out_count(db: Session, c: StockCount) -> CountOut:
    rows = db.execute(
        select(StockCountLine, Product, UnitOfMeasure)
        .join(
            Product,
            (Product.id == StockCountLine.product_id)
            & (Product.organization_id == StockCountLine.organization_id),
        )
        .join(UnitOfMeasure, UnitOfMeasure.id == Product.unit_id)
        .where(StockCountLine.count_id == c.id)
        .order_by(Product.sku)
    ).all()
    wh = db.get(Warehouse, c.warehouse_id)
    names = _names(db, {c.created_by, c.posted_by})
    return CountOut(
        id=c.id,
        status=c.status,
        warehouse=NameRef(id=c.warehouse_id, name=str(wh.name) if wh else ""),
        notes=c.notes,
        version=c.version,
        operation_id=c.operation_id,
        lines=[
            CountLineOut(
                product_id=p.id,
                sku=str(p.sku),
                name=p.name,
                unit=_unit_ref(u),
                expected_quantity=qty_str(ln.expected_quantity),
                counted_quantity=qty_str(ln.counted_quantity)
                if ln.counted_quantity is not None
                else None,
                applied_delta=qty_str(ln.applied_delta) if ln.applied_delta is not None else None,
            )
            for ln, p, u in rows
        ],
        created_by_name=names.get(c.created_by),
        posted_by_name=names.get(c.posted_by) if c.posted_by else None,
        posted_at=c.posted_at,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def list_counts(
    db: Session, principal: Principal, org_id: uuid.UUID, lq: ListQuery, *, status: str
) -> Page[CountListItem]:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    stmt = select(StockCount).where(StockCount.organization_id == org_id)
    if status in ("IN_PROGRESS", "POSTED"):
        stmt = stmt.where(StockCount.status == status)
    stmt = search(stmt, lq.q, StockCount.notes)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map={"created_at": StockCount.created_at},
        default_sort="created_at",
        id_column=StockCount.id,
        default_order="desc",
    )
    wh = {
        w.id: str(w.name)
        for w in db.scalars(select(Warehouse).where(Warehouse.organization_id == org_id))
    }
    stats = {
        r[0]: (int(r[1]), int(r[2]))
        for r in db.execute(
            select(
                StockCountLine.count_id,
                func.count(),
                func.count(StockCountLine.counted_quantity),
            )
            .where(StockCountLine.count_id.in_([r.id for r in rows]))
            .group_by(StockCountLine.count_id)
        )
    }
    return Page[CountListItem](
        items=[
            CountListItem(
                id=r.id,
                status=r.status,
                warehouse_name=wh.get(r.warehouse_id, ""),
                lines_count=stats.get(r.id, (0, 0))[0],
                counted_count=stats.get(r.id, (0, 0))[1],
                created_at=r.created_at,
                posted_at=r.posted_at,
            )
            for r in rows
        ],
        total=total,
        page=lq.page,
        page_size=lq.page_size,
    )


def get_count(db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID) -> CountOut:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    return _out_count(db, _load(db, org_id, cid))


def create_count(
    db: Session, principal: Principal, org_id: uuid.UUID, data: CountCreate
) -> CountOut:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    wh = db.scalar(
        select(Warehouse).where(
            Warehouse.id == data.warehouse_id, Warehouse.organization_id == org_id
        )
    )
    if wh is None:
        raise _fail(422, "warehouse_not_found", "Склад не найден.", "warehouse_id")
    if wh.archived_at is not None:
        raise _fail(422, "warehouse_archived", "Склад в архиве.", "warehouse_id")
    c = StockCount(
        id=new_id(),
        organization_id=org_id,
        warehouse_id=wh.id,
        status="IN_PROGRESS",
        notes=data.notes,
        created_by=principal.user.id,
    )
    db.add(c)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409,
            "count_already_in_progress",
            "По этому складу уже идёт инвентаризация.",
            "warehouse_id",
        ) from exc
    balances = db.scalars(
        select(StockBalance)
        .where(
            StockBalance.organization_id == org_id,
            StockBalance.warehouse_id == wh.id,
            StockBalance.quantity != 0,
        )
        .limit(MAX_LINES + 1)
    ).all()
    if len(balances) > MAX_LINES:
        db.rollback()
        raise _fail(
            422,
            "too_many_products",
            f"На складе больше {MAX_LINES} позиций: инвентаризация по частям пока не поддерживается.",
        )
    for b in balances:
        db.add(
            StockCountLine(
                id=new_id(),
                organization_id=org_id,
                count_id=c.id,
                product_id=b.product_id,
                expected_quantity=b.quantity,
            )
        )
    audit.record(
        db,
        action="stock_count.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_count",
        object_id=c.id,
        after={"warehouse_id": str(wh.id), "lines": len(balances)},
    )
    db.commit()
    return _out_count(db, c)


def update_count(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID, data: CountUpdate
) -> CountOut:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    c = _load(db, org_id, cid, lock=True)
    if c.version != data.expected_version:
        raise _conflict()
    if c.status != "IN_PROGRESS":
        raise AppError(409, "count_not_in_progress", "Проведённую инвентаризацию изменить нельзя.")
    fields = data.model_fields_set
    if "notes" in fields:
        c.notes = data.notes
    if data.lines is not None:
        _replace_lines(db, org_id, c, data.lines)
    c.version += 1
    c.updated_at = utcnow()
    audit.record(
        db,
        action="stock_count.updated",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_count",
        object_id=c.id,
        after={"lines": len(data.lines) if data.lines is not None else None},
    )
    db.commit()
    return _out_count(db, c)


def _replace_lines(db: Session, org_id: uuid.UUID, c: StockCount, lines: list[CountLineIn]) -> None:
    ids = [ln.product_id for ln in lines]
    if len(set(ids)) != len(ids):
        raise _fail(422, "duplicate_product", "Товар указан в инвентаризации дважды.", "lines")
    products = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.id.in_(set(ids)), Product.organization_id == org_id)
        )
    }
    existing = {
        ln.product_id: ln
        for ln in db.scalars(select(StockCountLine).where(StockCountLine.count_id == c.id))
    }
    current = {
        b.product_id: b.quantity
        for b in db.scalars(
            select(StockBalance).where(
                StockBalance.organization_id == org_id,
                StockBalance.warehouse_id == c.warehouse_id,
                StockBalance.product_id.in_(set(ids)),
            )
        )
    }
    for i, ln in enumerate(lines, 1):
        p = products.get(ln.product_id)
        field = f"lines.{i}.product_id"
        if p is None:
            raise _fail(422, "product_not_found", "Товар не найден.", field, position=i)
        if p.kind != "goods":
            raise _fail(
                422,
                "product_not_goods",
                "Услуга не участвует в складском учёте.",
                field,
                position=i,
            )
        if p.archived_at is not None and ln.product_id not in existing:
            raise _fail(422, "product_archived", "Товар в архиве.", field, position=i)
    db.execute(delete(StockCountLine).where(StockCountLine.count_id == c.id))
    db.flush()
    for ln in lines:
        prev = existing.get(ln.product_id)
        db.add(
            StockCountLine(
                id=new_id(),
                organization_id=org_id,
                count_id=c.id,
                product_id=ln.product_id,
                # снимок сохраняется; для добавленного вручную товара — текущий остаток склада
                expected_quantity=prev.expected_quantity
                if prev
                else current.get(ln.product_id, Decimal(0)),
                counted_quantity=ln.counted_quantity,
            )
        )
    db.flush()


def delete_count(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID, expected_version: int
) -> None:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    c = _load(db, org_id, cid, lock=True)
    if c.version != expected_version:
        raise _conflict()
    if c.status != "IN_PROGRESS":
        raise AppError(409, "count_not_in_progress", "Проведённую инвентаризацию удалить нельзя.")
    db.execute(delete(StockCountLine).where(StockCountLine.count_id == c.id))
    db.delete(c)
    audit.record(
        db,
        action="stock_count.deleted",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_count",
        object_id=cid,
        before={"warehouse_id": str(c.warehouse_id)},
    )
    db.commit()


def post_count(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID, expected_version: int
) -> CountOut:
    acc = authorize(db, principal, org_id, rbac.STOCK_POST)
    c = _load(db, org_id, cid, lock=True)
    if c.status == "POSTED":
        raise AppError(409, "count_already_posted", "Инвентаризация уже проведена.")
    if c.version != expected_version:
        raise _conflict()
    wh = db.get(Warehouse, c.warehouse_id)
    if wh is None or wh.archived_at is not None:
        raise _fail(422, "warehouse_archived", "Склад в архиве.", "warehouse_id")
    lines = list(
        db.scalars(
            select(StockCountLine)
            .where(StockCountLine.count_id == c.id, StockCountLine.counted_quantity.is_not(None))
            .order_by(StockCountLine.product_id)
        )
    )
    # остатки блокируются в стабильном порядке; расхождение считается от текущего остатка
    for ln in lines:
        db.execute(
            pg_insert(StockBalance)
            .values(
                organization_id=org_id,
                warehouse_id=c.warehouse_id,
                product_id=ln.product_id,
                quantity=Decimal(0),
            )
            .on_conflict_do_nothing()
        )
    deltas: list[tuple[StockCountLine, Decimal]] = []
    for ln in lines:
        bal = db.scalar(
            select(StockBalance)
            .where(
                StockBalance.organization_id == org_id,
                StockBalance.warehouse_id == c.warehouse_id,
                StockBalance.product_id == ln.product_id,
            )
            .with_for_update()
        )
        assert bal is not None and ln.counted_quantity is not None
        delta = ln.counted_quantity - bal.quantity
        ln.applied_delta = delta
        if delta != 0:
            deltas.append((ln, delta))
    op: StockOperation | None = None
    if deltas:
        op = StockOperation(
            id=new_id(),
            organization_id=org_id,
            kind="count_adjustment",
            status="DRAFT",
            op_date=utcnow().date(),
            warehouse_id=c.warehouse_id,
            reference="Инвентаризация",
            created_by=principal.user.id,
        )
        db.add(op)
        db.flush()
        op_lines = [
            StockOperationLine(
                id=new_id(),
                organization_id=org_id,
                operation_id=op.id,
                position=i,
                product_id=ln.product_id,
                quantity=delta,
                is_adjustment=True,
            )
            for i, (ln, delta) in enumerate(deltas, 1)
        ]
        db.add_all(op_lines)
        db.flush()
        _apply_posting(db, principal, acc, op, op_lines, reverse=False)
    c.status = "POSTED"
    c.operation_id = op.id if op else None
    c.posted_by = principal.user.id
    c.posted_at = utcnow()
    c.version += 1
    audit.record(
        db,
        action="stock_count.posted",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_count",
        object_id=c.id,
        after={
            "warehouse_id": str(c.warehouse_id),
            "counted_lines": len(lines),
            "adjusted_lines": len(deltas),
            "operation_id": str(op.id) if op else None,
        },
    )
    db.commit()
    return _out_count(db, c)
