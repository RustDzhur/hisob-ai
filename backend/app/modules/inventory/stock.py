"""Use cases: склад (срез 4.1) — операции, движения, остатки, сверка.

Инварианты:
- движения только добавляются (триггер БД); остаток — производная таблица, меняется вместе с движением;
- проведение: блокировка операции и строк остатков (в стабильном порядке) в одной транзакции;
  повторное проведение невозможно (статус под блокировкой);
- политика отрицательных остатков — настройка организации, по умолчанию «запрещено»;
- учёт только КОЛИЧЕСТВ в единице товара; стоимости и проводок нет (нет утверждённой учётной политики).
"""

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.catalog.models import Product, UnitOfMeasure
from app.modules.documents.models import Document, DocumentLine
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.inventory.models import (
    StockBalance,
    StockMovement,
    StockOperation,
    StockOperationLine,
    Warehouse,
)
from app.modules.inventory.schemas import (
    BalanceOut,
    DocRef,
    FromDocumentIn,
    MovementOut,
    NameRef,
    OperationCreate,
    OperationListItem,
    OperationOut,
    OperationUpdate,
    ReconciliationOut,
    ReconciliationRow,
    ReverseIn,
    StockLineIn,
    StockLineOut,
    StockSettings,
    UnitRef,
)
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

POLICY_KEY = "negative_stock_policy"
DEFAULT_POLICY = "forbid"


def qty_str(d: Decimal) -> str:
    """Точная строка без экспоненты и лишних нулей: 100, 2.5, 0.001."""
    return format(d.normalize(), "f")


def _fail(
    http: int, code: str, message: str, field: str | None = None, /, **params: Any
) -> AppError:
    return AppError(
        http,
        code,
        message,
        [{"field": field or "", "code": code, "message": message, "params": params}],
    )


def _policy(org: Any) -> str:
    value = (org.settings or {}).get(POLICY_KEY, DEFAULT_POLICY)
    return value if value in ("forbid", "allow") else DEFAULT_POLICY


# ---------------- настройки ----------------


def get_settings(db: Session, principal: Principal, org_id: uuid.UUID) -> StockSettings:
    acc = authorize(db, principal, org_id, rbac.STOCK_READ)
    return StockSettings(negative_stock_policy=_policy(acc.org))


def update_settings(
    db: Session, principal: Principal, org_id: uuid.UUID, data: StockSettings
) -> StockSettings:
    acc = authorize(db, principal, org_id, rbac.ORG_MANAGE)
    before = _policy(acc.org)
    if before != data.negative_stock_policy:
        acc.org.settings = {**(acc.org.settings or {}), POLICY_KEY: data.negative_stock_policy}
        audit.record(
            db,
            action="stock.settings_updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="organization",
            object_id=org_id,
            before={POLICY_KEY: before},
            after={POLICY_KEY: data.negative_stock_policy},
        )
        db.commit()
    return StockSettings(negative_stock_policy=data.negative_stock_policy)


# ---------------- вспомогательное ----------------


def _unit_ref(u: UnitOfMeasure) -> UnitRef:
    return UnitRef(code=str(u.code), name_uz=u.name_uz, name_ru=u.name_ru, name_en=u.name_en)


def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return {
        r.id: r.full_name
        for r in db.execute(select(User.id, User.full_name).where(User.id.in_(real)))
    }


def _load(db: Session, org_id: uuid.UUID, oid: uuid.UUID, *, lock: bool = False) -> StockOperation:
    stmt = select(StockOperation).where(
        StockOperation.id == oid, StockOperation.organization_id == org_id
    )
    op = db.scalar(stmt.with_for_update() if lock else stmt)
    if op is None:
        raise AppError(404, "not_found", "Операция не найдена.")
    return op


def _out(db: Session, op: StockOperation) -> OperationOut:
    rows = db.execute(
        select(StockOperationLine, Product, UnitOfMeasure)
        .join(
            Product,
            (Product.id == StockOperationLine.product_id)
            & (Product.organization_id == StockOperationLine.organization_id),
        )
        .join(UnitOfMeasure, UnitOfMeasure.id == Product.unit_id)
        .where(StockOperationLine.operation_id == op.id)
        .order_by(StockOperationLine.position)
    ).all()
    wh = {
        w.id: w
        for w in db.scalars(
            select(Warehouse).where(
                Warehouse.id.in_({op.warehouse_id, op.to_warehouse_id or op.warehouse_id})
            )
        )
    }
    names = _names(db, {op.created_by, op.posted_by})
    doc = (
        db.scalar(select(Document).where(Document.id == op.source_document_id))
        if op.source_document_id
        else None
    )
    to_wh = wh.get(op.to_warehouse_id) if op.to_warehouse_id else None
    return OperationOut(
        id=op.id,
        kind=op.kind,
        status=op.status,
        op_date=op.op_date,
        warehouse=NameRef(id=op.warehouse_id, name=str(wh[op.warehouse_id].name)),
        to_warehouse=NameRef(id=to_wh.id, name=str(to_wh.name)) if to_wh else None,
        reference=op.reference,
        notes=op.notes,
        version=op.version,
        source_document=DocRef(id=doc.id, number=doc.number, doc_type=doc.doc_type)
        if doc
        else None,
        reverses_id=op.reverses_id,
        reversed_by_id=op.reversed_by_id,
        lines=[
            StockLineOut(
                position=ln.position,
                product_id=p.id,
                sku=str(p.sku),
                name=p.name,
                unit=_unit_ref(u),
                quantity=qty_str(ln.quantity),
            )
            for ln, p, u in rows
        ],
        created_by_name=names.get(op.created_by),
        posted_by_name=names.get(op.posted_by) if op.posted_by else None,
        posted_at=op.posted_at,
        created_at=op.created_at,
        updated_at=op.updated_at,
    )


def _validate(
    db: Session,
    org_id: uuid.UUID,
    kind: str,
    warehouse_id: uuid.UUID,
    to_warehouse_id: uuid.UUID | None,
    lines: list[StockLineIn],
    *,
    reversal: bool = False,
) -> None:
    """Ссылки на склады и товары: свои, не архивные, товары — не услуги.

    Для сторно архивность товара не проверяется (возвращаем прежнее состояние), склада — проверяется.
    """
    if kind == "transfer":
        if to_warehouse_id is None:
            raise _fail(
                422, "to_warehouse_required", "Укажите склад назначения.", "to_warehouse_id"
            )
        if to_warehouse_id == warehouse_id:
            raise _fail(
                422, "warehouses_must_differ", "Склады должны различаться.", "to_warehouse_id"
            )
    elif to_warehouse_id is not None:
        raise _fail(
            422,
            "to_warehouse_not_allowed",
            "Склад назначения только для перемещения.",
            "to_warehouse_id",
        )
    for field, wid in (("warehouse_id", warehouse_id), ("to_warehouse_id", to_warehouse_id)):
        if wid is None:
            continue
        w = db.scalar(
            select(Warehouse).where(Warehouse.id == wid, Warehouse.organization_id == org_id)
        )
        if w is None:
            raise _fail(422, "warehouse_not_found", "Склад не найден.", field)
        if w.archived_at is not None:
            raise _fail(422, "warehouse_archived", "Склад в архиве.", field)
    product_ids = {ln.product_id for ln in lines}
    found = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.id.in_(product_ids), Product.organization_id == org_id)
        )
    }
    for i, ln in enumerate(lines, 1):
        p = found.get(ln.product_id)
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
        if p.archived_at is not None and not reversal:
            raise _fail(422, "product_archived", "Товар в архиве.", field, position=i)


def _replace_lines(
    db: Session, org_id: uuid.UUID, op_id: uuid.UUID, lines: list[StockLineIn]
) -> None:
    db.execute(delete(StockOperationLine).where(StockOperationLine.operation_id == op_id))
    db.flush()
    for i, ln in enumerate(lines, 1):
        db.add(
            StockOperationLine(
                id=new_id(),
                organization_id=org_id,
                operation_id=op_id,
                position=i,
                product_id=ln.product_id,
                quantity=ln.quantity,
            )
        )
    db.flush()


def _snapshot(op: StockOperation, lines: int) -> dict[str, Any]:
    return {
        "source_document_id": str(op.source_document_id) if op.source_document_id else None,
        "kind": op.kind,
        "status": op.status,
        "op_date": op.op_date.isoformat(),
        "warehouse_id": str(op.warehouse_id),
        "to_warehouse_id": str(op.to_warehouse_id) if op.to_warehouse_id else None,
        "reference": op.reference,
        "lines": lines,
    }


# ---------------- операции ----------------

SORTABLE_OPS: dict[str, SQLColumnExpression[Any]] = {
    "op_date": StockOperation.op_date,
    "created_at": StockOperation.created_at,
}


def list_operations(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    status: str,
    kind: str | None,
    warehouse_id: uuid.UUID | None,
    document_id: uuid.UUID | None = None,
) -> Page[OperationListItem]:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    stmt = select(StockOperation).where(StockOperation.organization_id == org_id)
    if status in ("DRAFT", "POSTED"):
        stmt = stmt.where(StockOperation.status == status)
    if kind:
        stmt = stmt.where(StockOperation.kind == kind)
    if document_id:
        stmt = stmt.where(StockOperation.source_document_id == document_id)
    if warehouse_id:
        stmt = stmt.where(
            (StockOperation.warehouse_id == warehouse_id)
            | (StockOperation.to_warehouse_id == warehouse_id)
        )
    stmt = search(stmt, lq.q, StockOperation.reference, StockOperation.notes)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map=SORTABLE_OPS,
        default_sort="op_date",
        id_column=StockOperation.id,
        default_order="desc",
    )
    wh = {
        w.id: str(w.name)
        for w in db.scalars(select(Warehouse).where(Warehouse.organization_id == org_id))
    }
    counts: dict[uuid.UUID, int] = {
        r[0]: int(r[1])
        for r in db.execute(
            select(StockOperationLine.operation_id, func.count())
            .where(StockOperationLine.operation_id.in_([r.id for r in rows]))
            .group_by(StockOperationLine.operation_id)
        )
    }
    return Page[OperationListItem](
        items=[
            OperationListItem(
                id=r.id,
                kind=r.kind,
                status=r.status,
                op_date=r.op_date,
                warehouse_name=wh.get(r.warehouse_id, ""),
                to_warehouse_name=wh.get(r.to_warehouse_id) if r.to_warehouse_id else None,
                reference=r.reference,
                lines_count=int(counts.get(r.id, 0)),
                is_reversal=r.reverses_id is not None,
                reversed=r.reversed_by_id is not None,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        page=lq.page,
        page_size=lq.page_size,
    )


def get_operation(
    db: Session, principal: Principal, org_id: uuid.UUID, oid: uuid.UUID
) -> OperationOut:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    return _out(db, _load(db, org_id, oid))


def create_operation(
    db: Session, principal: Principal, org_id: uuid.UUID, data: OperationCreate
) -> OperationOut:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    _validate(db, org_id, data.kind, data.warehouse_id, data.to_warehouse_id, data.lines)
    op = StockOperation(
        id=new_id(),
        organization_id=org_id,
        kind=data.kind,
        status="DRAFT",
        op_date=data.op_date,
        warehouse_id=data.warehouse_id,
        to_warehouse_id=data.to_warehouse_id,
        reference=data.reference,
        notes=data.notes,
        created_by=principal.user.id,
    )
    db.add(op)
    db.flush()
    _replace_lines(db, org_id, op.id, data.lines)
    audit.record(
        db,
        action="stock_operation.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_operation",
        object_id=op.id,
        after=_snapshot(op, len(data.lines)),
    )
    db.commit()
    return _out(db, op)


def _conflict() -> AppError:
    return AppError(
        409, "operation_conflict", "Операция изменена другим пользователем. Обновите страницу."
    )


def update_operation(
    db: Session, principal: Principal, org_id: uuid.UUID, oid: uuid.UUID, data: OperationUpdate
) -> OperationOut:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    op = _load(db, org_id, oid, lock=True)
    if op.version != data.expected_version:
        raise _conflict()
    if op.status != "DRAFT":
        raise AppError(409, "operation_not_draft", "Проведённую операцию изменить нельзя.")
    before_lines = (
        db.scalar(
            select(func.count())
            .select_from(StockOperationLine)
            .where(StockOperationLine.operation_id == op.id)
        )
        or 0
    )
    before = _snapshot(op, int(before_lines))
    fields = data.model_fields_set
    # сначала проверяем итоговое состояние, и только потом меняем объект (иначе автосброс сессии
    # упрёт неверные значения в ограничения БД и вместо 422 будет 500)
    new_date = data.op_date if "op_date" in fields and data.op_date is not None else op.op_date
    new_wh = (
        data.warehouse_id
        if "warehouse_id" in fields and data.warehouse_id is not None
        else op.warehouse_id
    )
    new_to = data.to_warehouse_id if "to_warehouse_id" in fields else op.to_warehouse_id
    if data.lines is not None:
        lines_in = data.lines
    else:
        lines_in = [
            StockLineIn(product_id=ln.product_id, quantity=ln.quantity)
            for ln in db.scalars(
                select(StockOperationLine)
                .where(StockOperationLine.operation_id == op.id)
                .order_by(StockOperationLine.position)
            )
        ]
    _validate(db, org_id, op.kind, new_wh, new_to, lines_in)
    op.op_date, op.warehouse_id, op.to_warehouse_id = new_date, new_wh, new_to
    if "reference" in fields:
        op.reference = data.reference
    if "notes" in fields:
        op.notes = data.notes
    if data.lines is not None:
        _replace_lines(db, org_id, op.id, data.lines)
    op.version += 1
    op.updated_at = utcnow()
    audit.record(
        db,
        action="stock_operation.updated",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_operation",
        object_id=op.id,
        before=before,
        after=_snapshot(op, len(lines_in)),
    )
    db.commit()
    return _out(db, op)


def delete_operation(
    db: Session, principal: Principal, org_id: uuid.UUID, oid: uuid.UUID, expected_version: int
) -> None:
    authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    op = _load(db, org_id, oid, lock=True)
    if op.version != expected_version:
        raise _conflict()
    if op.status != "DRAFT":
        raise AppError(409, "operation_not_draft", "Проведённую операцию удалить нельзя.")
    n = db.scalar(
        select(func.count())
        .select_from(StockOperationLine)
        .where(StockOperationLine.operation_id == op.id)
    )
    snap = _snapshot(op, int(n or 0))
    db.execute(delete(StockOperationLine).where(StockOperationLine.operation_id == op.id))
    db.delete(op)
    audit.record(
        db,
        action="stock_operation.deleted",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_operation",
        object_id=oid,
        before=snap,
    )
    db.commit()


def post_operation(
    db: Session, principal: Principal, org_id: uuid.UUID, oid: uuid.UUID, expected_version: int
) -> OperationOut:
    acc = authorize(db, principal, org_id, rbac.STOCK_POST)
    op = _load(db, org_id, oid, lock=True)
    if op.status == "POSTED":
        raise AppError(409, "operation_already_posted", "Операция уже проведена.")
    if op.version != expected_version:
        raise _conflict()
    lines = list(
        db.scalars(
            select(StockOperationLine)
            .where(StockOperationLine.operation_id == op.id)
            .order_by(StockOperationLine.position)
        )
    )
    if not lines:
        raise _fail(422, "lines_required", "Добавьте хотя бы одну строку.", "lines")
    _validate(
        db,
        org_id,
        op.kind,
        op.warehouse_id,
        op.to_warehouse_id,
        [StockLineIn(product_id=ln.product_id, quantity=ln.quantity) for ln in lines],
    )

    _apply_posting(db, principal, acc, op, lines, reverse=False)
    db.commit()
    return _out(db, op)


def _legs(
    kind: str, wh: uuid.UUID, to: uuid.UUID | None, qty: Decimal, reverse: bool
) -> list[tuple[uuid.UUID, Decimal]]:
    """Изменения по складам для одной строки. Приход и возврат от клиента увеличивают, расход и возврат
    поставщику уменьшают; перемещение — с одного склада на другой. Сторно меняет знаки."""
    legs: list[tuple[uuid.UUID, Decimal]]
    if kind in ("receipt", "customer_return"):
        legs = [(wh, qty)]
    elif kind in ("issue", "supplier_return"):
        legs = [(wh, -qty)]
    else:  # transfer
        assert to is not None
        legs = [(wh, -qty), (to, qty)]
    return [(w, -d) for w, d in legs] if reverse else legs


def _apply_posting(
    db: Session,
    principal: Principal,
    acc: Any,
    op: StockOperation,
    lines: list[StockOperationLine],
    *,
    reverse: bool,
) -> None:
    """Единый путь проведения (обычного и сторно): блокировка остатков, проверка нехватки, движения, статус.

    Коммит выполняет вызывающий вместе с остальными изменениями той же транзакции.
    """
    org_id = op.organization_id
    # изменения по парам (склад, товар); для движений — построчно, для остатков — суммарно
    movements: list[tuple[uuid.UUID, uuid.UUID, Decimal]] = []
    totals: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(Decimal)
    for ln in lines:
        for wid, delta in _legs(op.kind, op.warehouse_id, op.to_warehouse_id, ln.quantity, reverse):
            movements.append((wid, ln.product_id, delta))
            totals[(wid, ln.product_id)] += delta

    forbid_negative = _policy(acc.org) == "forbid"
    shortages: list[dict[str, Any]] = []
    keys = sorted(
        totals, key=lambda k: (str(k[0]), str(k[1]))
    )  # стабильный порядок: без взаимных блокировок
    for wid, pid in keys:
        db.execute(
            pg_insert(StockBalance)
            .values(organization_id=org_id, warehouse_id=wid, product_id=pid, quantity=Decimal(0))
            .on_conflict_do_nothing()
        )
    for wid, pid in keys:
        bal = db.scalar(
            select(StockBalance)
            .where(
                StockBalance.organization_id == org_id,
                StockBalance.warehouse_id == wid,
                StockBalance.product_id == pid,
            )
            .with_for_update()
        )
        assert bal is not None
        new_qty = bal.quantity + totals[(wid, pid)]
        if new_qty < 0 and forbid_negative:
            shortages.append(
                {
                    "warehouse_id": str(wid),
                    "product_id": str(pid),
                    "available": qty_str(bal.quantity),
                    "required": qty_str(-totals[(wid, pid)]),
                }
            )
        bal.quantity = new_qty
    if shortages:
        db.rollback()
        p_ids = {uuid.UUID(s["product_id"]) for s in shortages}
        skus = {p.id: str(p.sku) for p in db.scalars(select(Product).where(Product.id.in_(p_ids)))}
        for s in shortages:
            s["sku"] = skus.get(uuid.UUID(s["product_id"]), "")
        raise AppError(
            409,
            "insufficient_stock",
            "Недостаточно остатка для проведения.",
            [
                {
                    "field": "lines",
                    "code": "insufficient_stock",
                    "message": "Недостаточно остатка.",
                    "params": s,
                }
                for s in shortages
            ],
        )
    for wid, pid, delta in movements:
        db.add(
            StockMovement(
                id=new_id(),
                organization_id=org_id,
                operation_id=op.id,
                warehouse_id=wid,
                product_id=pid,
                quantity_delta=delta,
                op_date=op.op_date,
            )
        )
    op.status = "POSTED"
    op.posted_by = principal.user.id
    op.posted_at = utcnow()
    op.version += 1
    audit.record(
        db,
        action="stock_operation.reversed" if reverse else "stock_operation.posted",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_operation",
        object_id=op.id,
        after={
            **_snapshot(op, len(lines)),
            "reverses_id": str(op.reverses_id) if op.reverses_id else None,
        },
    )


def _forbidden() -> AppError:
    return AppError(403, "forbidden", "Недостаточно прав для этого действия.")


def create_from_document(
    db: Session, principal: Principal, org_id: uuid.UUID, data: FromDocumentIn
) -> OperationOut:
    """Черновик складской операции по строкам утверждённого документа. Сам не проводится: проводит человек.

    Каждая строка должна быть готова к учёту: товар выбран, количество указано, единица совпадает
    с единицей товара (пересчёта между единицами нет — единицу товара нужно привести к документу).
    Строки-услуги в складской учёт не попадают.
    """
    acc = authorize(db, principal, org_id, rbac.STOCK_MANAGE)
    if rbac.DOCUMENTS_READ not in acc.permissions:
        raise _forbidden()
    doc = db.scalar(
        select(Document)
        .where(Document.id == data.document_id, Document.organization_id == org_id)
        .with_for_update()
    )
    if doc is None:
        raise AppError(404, "not_found", "Документ не найден.")
    if doc.status != "APPROVED":
        raise _fail(
            409,
            "document_not_approved",
            "Складскую операцию можно оформить только по утверждённому документу.",
        )
    rows = db.execute(
        select(DocumentLine, Product)
        .outerjoin(
            Product,
            (Product.id == DocumentLine.product_id)
            & (Product.organization_id == DocumentLine.organization_id),
        )
        .where(DocumentLine.document_id == doc.id)
        .order_by(DocumentLine.position)
    ).all()
    problems: list[dict[str, Any]] = []
    lines: list[StockLineIn] = []
    for ln, p in rows:
        pos = ln.position
        field = f"lines.{pos}"
        if p is not None and p.kind == "service":
            continue  # услуги не участвуют в складском учёте
        if p is None:
            problems.append(
                _problem("line_product_missing", "Выберите товар в строке документа.", field, pos)
            )
        elif ln.quantity is None or ln.quantity <= 0:
            problems.append(
                _problem(
                    "line_quantity_missing", "Укажите количество в строке документа.", field, pos
                )
            )
        elif ln.unit_id is not None and ln.unit_id != p.unit_id:
            problems.append(
                _problem(
                    "line_unit_mismatch",
                    "Единица в строке отличается от единицы товара: приведите единицу товара к документу.",
                    field,
                    pos,
                    sku=str(p.sku),
                )
            )
        else:
            lines.append(StockLineIn(product_id=p.id, quantity=ln.quantity))
    if problems:
        raise AppError(
            422,
            "document_lines_not_ready",
            "Строки документа не готовы к складскому учёту.",
            problems,
        )
    if not lines:
        raise _fail(422, "no_stock_lines", "В документе нет товарных строк для складского учёта.")
    _validate(db, org_id, data.kind, data.warehouse_id, None, lines)
    op = StockOperation(
        id=new_id(),
        organization_id=org_id,
        kind=data.kind,
        status="DRAFT",
        op_date=data.op_date or doc.doc_date or utcnow().date(),
        warehouse_id=data.warehouse_id,
        reference=doc.number,
        source_document_id=doc.id,
        created_by=principal.user.id,
    )
    db.add(op)
    try:
        db.flush()
    except IntegrityError as exc:  # гонка или повтор: по документу уже есть операция этого вида
        db.rollback()
        raise _fail(
            409,
            "document_already_in_stock",
            "По этому документу уже оформлена такая складская операция.",
        ) from exc
    _replace_lines(db, org_id, op.id, lines)
    audit.record(
        db,
        action="stock_operation.created_from_document",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="stock_operation",
        object_id=op.id,
        after=_snapshot(op, len(lines)),
    )
    db.commit()
    return _out(db, op)


def _problem(code: str, message: str, field: str, position: int, **params: Any) -> dict[str, Any]:
    return {
        "field": field,
        "code": code,
        "message": message,
        "params": {"position": position, **params},
    }


def reverse_operation(
    db: Session, principal: Principal, org_id: uuid.UUID, oid: uuid.UUID, data: ReverseIn
) -> OperationOut:
    """Сторно: новая проведённая операция с противоположными движениями; исходная остаётся в истории."""
    acc = authorize(db, principal, org_id, rbac.STOCK_POST)
    orig = _load(db, org_id, oid, lock=True)
    if orig.version != data.expected_version:
        raise _conflict()
    if orig.status != "POSTED":
        raise AppError(
            409, "operation_not_posted", "Сторнировать можно только проведённую операцию."
        )
    if orig.reverses_id is not None:
        raise AppError(409, "operation_is_reversal", "Сторно-операцию сторнировать нельзя.")
    if orig.reversed_by_id is not None:
        raise AppError(409, "operation_already_reversed", "Операция уже сторнирована.")
    src_lines = list(
        db.scalars(
            select(StockOperationLine)
            .where(StockOperationLine.operation_id == orig.id)
            .order_by(StockOperationLine.position)
        )
    )
    _validate(
        db,
        org_id,
        orig.kind,
        orig.warehouse_id,
        orig.to_warehouse_id,
        [StockLineIn(product_id=ln.product_id, quantity=ln.quantity) for ln in src_lines],
        reversal=True,
    )
    rev = StockOperation(
        id=new_id(),
        organization_id=org_id,
        kind=orig.kind,
        status="POSTED",  # ограничения БД: сторно существует только проведённым
        posted_by=principal.user.id,
        posted_at=utcnow(),
        op_date=data.op_date or utcnow().date(),
        warehouse_id=orig.warehouse_id,
        to_warehouse_id=orig.to_warehouse_id,
        reference=orig.reference,
        notes=None,
        reverses_id=orig.id,
        created_by=principal.user.id,
    )
    db.add(rev)
    db.flush()
    rev_lines = [
        StockOperationLine(
            id=new_id(),
            organization_id=org_id,
            operation_id=rev.id,
            position=ln.position,
            product_id=ln.product_id,
            quantity=ln.quantity,
        )
        for ln in src_lines
    ]
    db.add_all(rev_lines)
    db.flush()
    _apply_posting(db, principal, acc, rev, rev_lines, reverse=True)
    orig.reversed_by_id = rev.id
    orig.version += 1
    db.commit()
    return _out(db, rev)


# ---------------- остатки, движения, сверка ----------------

SORTABLE_BAL: dict[str, SQLColumnExpression[Any]] = {
    "sku": Product.sku,
    "name": Product.name,
    "quantity": StockBalance.quantity,
    "warehouse": Warehouse.name,
}


def list_balances(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    warehouse_id: uuid.UUID | None,
    include_zero: bool,
) -> Page[BalanceOut]:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    stmt: Any = (
        select(StockBalance, Product, Warehouse, UnitOfMeasure)
        .join(
            Product,
            (Product.id == StockBalance.product_id)
            & (Product.organization_id == StockBalance.organization_id),
        )
        .join(
            Warehouse,
            (Warehouse.id == StockBalance.warehouse_id)
            & (Warehouse.organization_id == StockBalance.organization_id),
        )
        .join(UnitOfMeasure, UnitOfMeasure.id == Product.unit_id)
        .where(StockBalance.organization_id == org_id)
    )
    if warehouse_id:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    if not include_zero:
        stmt = stmt.where(StockBalance.quantity != 0)
    stmt = search(stmt, lq.q, Product.sku, Product.name)
    # paginate ожидает select одной сущности: берём пары ключей и догружаем
    key_stmt = stmt.with_only_columns(StockBalance.warehouse_id, StockBalance.product_id)
    page_rows, total = _paginate_rows(db, stmt, key_stmt, lq)
    return Page[BalanceOut](
        items=[
            BalanceOut(
                warehouse=NameRef(id=w.id, name=str(w.name)),
                product_id=p.id,
                sku=str(p.sku),
                name=p.name,
                unit=_unit_ref(u),
                quantity=qty_str(b.quantity),
            )
            for b, p, w, u in page_rows
        ],
        total=total,
        page=lq.page,
        page_size=lq.page_size,
    )


def _paginate_rows(db: Session, stmt: Any, key_stmt: Any, lq: ListQuery) -> tuple[list[Any], int]:
    key = lq.sort or "name"
    column = SORTABLE_BAL.get(key)
    if column is None:
        raise _fail(422, "invalid_sort", "Недопустимое поле сортировки.", "sort")
    total = db.scalar(select(func.count()).select_from(key_stmt.order_by(None).subquery())) or 0
    ordering = column.desc() if lq.order == "desc" else column.asc()
    rows = db.execute(
        stmt.order_by(ordering, StockBalance.warehouse_id, StockBalance.product_id)
        .limit(lq.page_size)
        .offset((lq.page - 1) * lq.page_size)
    ).all()
    return [tuple(r) for r in rows], int(total)


def list_movements(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    product_id: uuid.UUID | None,
    warehouse_id: uuid.UUID | None,
) -> Page[MovementOut]:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    stmt: Any = (
        select(StockMovement, StockOperation, Product, Warehouse, UnitOfMeasure)
        .join(
            StockOperation,
            (StockOperation.id == StockMovement.operation_id)
            & (StockOperation.organization_id == StockMovement.organization_id),
        )
        .join(
            Product,
            (Product.id == StockMovement.product_id)
            & (Product.organization_id == StockMovement.organization_id),
        )
        .join(
            Warehouse,
            (Warehouse.id == StockMovement.warehouse_id)
            & (Warehouse.organization_id == StockMovement.organization_id),
        )
        .join(UnitOfMeasure, UnitOfMeasure.id == Product.unit_id)
        .where(StockMovement.organization_id == org_id)
    )
    if product_id:
        stmt = stmt.where(StockMovement.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    stmt = search(stmt, lq.q, Product.sku, Product.name, StockOperation.reference)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(
        stmt.order_by(StockMovement.occurred_at.desc(), StockMovement.id)
        .limit(lq.page_size)
        .offset((lq.page - 1) * lq.page_size)
    ).all()
    return Page[MovementOut](
        items=[
            MovementOut(
                id=m.id,
                operation_id=o.id,
                kind=o.kind,
                reference=o.reference,
                warehouse=NameRef(id=w.id, name=str(w.name)),
                product_id=p.id,
                sku=str(p.sku),
                name=p.name,
                unit=_unit_ref(u),
                quantity_delta=qty_str(m.quantity_delta),
                op_date=m.op_date,
                occurred_at=m.occurred_at,
            )
            for m, o, p, w, u in rows
        ],
        total=int(total),
        page=lq.page,
        page_size=lq.page_size,
    )


def find_mismatches(
    db: Session, org_id: uuid.UUID
) -> tuple[int, list[tuple[uuid.UUID, uuid.UUID, Decimal, Decimal]]]:
    """Сверка: остаток должен равняться сумме движений по каждой паре (склад, товар)."""
    sums = (
        select(
            StockMovement.warehouse_id.label("w"),
            StockMovement.product_id.label("p"),
            func.sum(StockMovement.quantity_delta).label("s"),
        )
        .where(StockMovement.organization_id == org_id)
        .group_by(StockMovement.warehouse_id, StockMovement.product_id)
        .subquery()
    )
    bal = (
        select(
            StockBalance.warehouse_id.label("w"),
            StockBalance.product_id.label("p"),
            StockBalance.quantity.label("q"),
        )
        .where(StockBalance.organization_id == org_id)
        .subquery()
    )
    joined = (
        select(
            func.coalesce(bal.c.w, sums.c.w),
            func.coalesce(bal.c.p, sums.c.p),
            func.coalesce(bal.c.q, 0),
            func.coalesce(sums.c.s, 0),
        )
        .select_from(bal.outerjoin(sums, (bal.c.w == sums.c.w) & (bal.c.p == sums.c.p), full=True))
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(joined)) or 0
    bad = db.execute(select(joined).where(joined.c[2] != joined.c[3])).all()
    return int(total), [(r[0], r[1], Decimal(r[2]), Decimal(r[3])) for r in bad]


def reconciliation(db: Session, principal: Principal, org_id: uuid.UUID) -> ReconciliationOut:
    authorize(db, principal, org_id, rbac.STOCK_READ)
    total, bad = find_mismatches(db, org_id)
    rows: list[ReconciliationRow] = []
    for wid, pid, q, s in bad:
        w = db.get(Warehouse, wid)
        p = db.get(Product, pid)
        rows.append(
            ReconciliationRow(
                warehouse=NameRef(id=wid, name=str(w.name) if w else ""),
                product_id=pid,
                sku=str(p.sku) if p else "",
                name=p.name if p else "",
                balance=qty_str(q),
                movements_sum=qty_str(s),
            )
        )
    return ReconciliationOut(ok=not rows, checked_pairs=total, mismatches=rows)


def has_stock(db: Session, org_id: uuid.UUID, warehouse_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(StockBalance)
            .where(
                StockBalance.organization_id == org_id,
                StockBalance.warehouse_id == warehouse_id,
                StockBalance.quantity != 0,
            )
        )
        or 0
    ) > 0
