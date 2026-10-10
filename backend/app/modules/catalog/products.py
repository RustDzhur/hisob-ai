"""Use cases: товары и услуги."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.catalog.models import Product, ProductIdentifier, UnitOfMeasure
from app.modules.catalog.schemas import ProductCreate, ProductOut, ProductUnit, ProductUpdate
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import Currency, OrganizationCurrency
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, like_pattern, paginate

SORTABLE: dict[str, SQLColumnExpression[Any]] = {
    "name": Product.name,
    "sku": Product.sku,
    "created_at": Product.created_at,
}  # сортировки по цене нет намеренно: цены в разных валютах несравнимы без курса


def _field_error(status: int, code: str, field: str, message: str, **params: Any) -> AppError:
    return AppError(
        status,
        code,
        message,
        [{"field": field, "code": code, "message": message, "params": params}],
    )


# ---------- проверки зависимостей ----------


def _unit(db: Session, org_id: uuid.UUID, unit_id: uuid.UUID) -> UnitOfMeasure:
    unit = db.scalar(
        select(UnitOfMeasure).where(
            UnitOfMeasure.id == unit_id,
            or_(UnitOfMeasure.organization_id.is_(None), UnitOfMeasure.organization_id == org_id),
            UnitOfMeasure.is_active.is_(True),
        )
    )
    if unit is None:
        raise _field_error(422, "unit_unavailable", "unit_id", "Единица измерения недоступна.")
    return unit


def _check_price(db: Session, org_id: uuid.UUID, price: Decimal | None, code: str | None) -> None:
    if price is None or code is None:
        return
    currency = db.get(Currency, code)
    if currency is None:
        raise _field_error(422, "currency_unknown", "price_currency", "Неизвестная валюта.")
    enabled = db.scalar(
        select(OrganizationCurrency.enabled).where(
            OrganizationCurrency.organization_id == org_id,
            OrganizationCurrency.currency_code == code,
        )
    )
    if not enabled:
        raise _field_error(
            422, "currency_not_enabled", "price_currency", "Валюта не включена в организации."
        )
    # Знаков больше, чем у валюты — ошибка, а не молчаливое округление (правила округления — Q6).
    if -price.as_tuple().exponent > currency.minor_units:  # type: ignore[operator]
        raise _field_error(
            422,
            "price_decimals",
            "price",
            "Слишком много знаков после запятой.",
            max=currency.minor_units,
        )


def _barcode_conflicts(
    db: Session, org_id: uuid.UUID, values: list[str], exclude_product: uuid.UUID | None
) -> None:
    if not values:
        return
    rows = db.execute(
        select(ProductIdentifier.value, Product.sku, Product.id)
        .join(Product, Product.id == ProductIdentifier.product_id)
        .where(
            ProductIdentifier.organization_id == org_id,
            ProductIdentifier.kind == "barcode",
            ProductIdentifier.value.in_(values),
        )
    ).all()
    for value, sku, pid in rows:
        if pid != exclude_product:
            raise _field_error(
                409,
                "barcode_taken",
                "barcodes",
                "Штрихкод уже используется.",
                value=str(value),
                sku=str(sku),
            )


def _sku_taken(db: Session, org_id: uuid.UUID, sku: str, exclude: uuid.UUID | None) -> None:
    other = db.scalar(select(Product).where(Product.organization_id == org_id, Product.sku == sku))
    if other is not None and other.id != exclude:
        code = "product_sku_taken_archived" if other.archived_at else "product_sku_taken"
        raise _field_error(409, code, "sku", "Артикул уже используется.", name=other.name)


# ---------- сборка ответов ----------


def _fmt_price(price: Decimal | None, minor: int | None) -> str | None:
    if price is None:
        return None
    return str(
        price.quantize(Decimal(1).scaleb(-(minor or 0)))
    )  # знаков не больше, чем разрешено => без округления


def _outs(db: Session, products: list[Product]) -> list[ProductOut]:
    if not products:
        return []
    ids = [p.id for p in products]
    barcodes: dict[uuid.UUID, list[str]] = {}
    for pid, value in db.execute(
        select(ProductIdentifier.product_id, ProductIdentifier.value)
        .where(ProductIdentifier.product_id.in_(ids), ProductIdentifier.kind == "barcode")
        .order_by(ProductIdentifier.value)
    ):
        barcodes.setdefault(pid, []).append(str(value))
    units = {
        u.id: u
        for u in db.scalars(
            select(UnitOfMeasure).where(UnitOfMeasure.id.in_({p.unit_id for p in products}))
        )
    }
    minors = dict(
        db.execute(
            select(Currency.code, Currency.minor_units).where(
                Currency.code.in_({p.price_currency for p in products if p.price_currency})
            )
        ).all()
    )
    out = []
    for p in products:
        u = units.get(p.unit_id)
        if u is None:  # единицу видно всегда (системная или своя); защитная ветка
            raise RuntimeError("единица измерения товара недоступна")
        out.append(
            ProductOut(
                id=p.id,
                kind=p.kind,
                sku=str(p.sku),
                name=p.name,
                unit=ProductUnit(
                    id=u.id,
                    code=str(u.code),
                    name_uz=u.name_uz,
                    name_ru=u.name_ru,
                    name_en=u.name_en,
                ),
                price=_fmt_price(p.price, minors.get(p.price_currency or "")),
                price_currency=p.price_currency,
                ikpu=p.ikpu,
                description=p.description,
                barcodes=barcodes.get(p.id, []),
                archived=p.archived_at is not None,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
    return out


def _snapshot(out: ProductOut) -> dict[str, Any]:
    return {
        "kind": out.kind,
        "sku": out.sku,
        "name": out.name,
        "unit": out.unit.code,
        "price": out.price,
        "price_currency": out.price_currency,
        "ikpu": out.ikpu,
        "description": out.description,
        "barcodes": out.barcodes,
        "archived": out.archived,
    }


def _get(db: Session, org_id: uuid.UUID, pid: uuid.UUID, *, lock: bool = False) -> Product:
    stmt = select(Product).where(Product.id == pid, Product.organization_id == org_id)
    p = db.scalar(stmt.with_for_update() if lock else stmt)
    if p is None:
        raise AppError(404, "not_found", "Товар не найден.")
    return p


# ---------- запросы ----------


def list_products(
    db: Session, principal: Principal, org_id: uuid.UUID, lq: ListQuery, *, kind: str, status: str
) -> Page[ProductOut]:
    authorize(db, principal, org_id, rbac.CATALOG_READ)
    stmt = select(Product).where(Product.organization_id == org_id)
    if kind in ("goods", "service"):
        stmt = stmt.where(Product.kind == kind)
    if status == "active":
        stmt = stmt.where(Product.archived_at.is_(None))
    elif status == "archived":
        stmt = stmt.where(Product.archived_at.is_not(None))
    if lq.q:
        pat = like_pattern(lq.q)
        stmt = stmt.where(
            or_(
                Product.sku.ilike(pat, escape="\\"),
                Product.name.ilike(pat, escape="\\"),
                Product.ikpu.ilike(pat, escape="\\"),
                exists().where(
                    ProductIdentifier.product_id == Product.id,
                    ProductIdentifier.value.ilike(pat, escape="\\"),
                ),
            )
        )
    rows, total = paginate(
        db, stmt, lq, sort_map=SORTABLE, default_sort="name", id_column=Product.id
    )
    return Page[ProductOut](
        items=_outs(db, rows), total=total, page=lq.page, page_size=lq.page_size
    )


def get_product(db: Session, principal: Principal, org_id: uuid.UUID, pid: uuid.UUID) -> ProductOut:
    authorize(db, principal, org_id, rbac.CATALOG_READ)
    return _outs(db, [_get(db, org_id, pid)])[0]


def create_product(
    db: Session, principal: Principal, org_id: uuid.UUID, data: ProductCreate
) -> ProductOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    _unit(db, org_id, data.unit_id)
    _check_price(db, org_id, data.price, data.price_currency)
    _sku_taken(db, org_id, data.sku, None)
    _barcode_conflicts(db, org_id, data.barcodes, None)

    p = Product(
        id=new_id(),
        organization_id=org_id,
        kind=data.kind,
        sku=data.sku,
        name=data.name,
        unit_id=data.unit_id,
        price=data.price,
        price_currency=data.price_currency,
        ikpu=data.ikpu,
        description=data.description,
    )
    db.add(p)
    try:
        db.flush()
        for code in data.barcodes:
            db.add(
                ProductIdentifier(
                    id=new_id(), organization_id=org_id, product_id=p.id, kind="barcode", value=code
                )
            )
        db.flush()
    except IntegrityError as exc:  # гонка: артикул/штрихкод заняли параллельно
        db.rollback()
        raise AppError(409, "product_sku_taken", "Артикул или штрихкод уже используется.") from exc
    out = _outs(db, [p])[0]
    audit.record(
        db,
        action="product.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="product",
        object_id=p.id,
        after=_snapshot(out),
    )
    db.commit()
    return out


def update_product(
    db: Session, principal: Principal, org_id: uuid.UUID, pid: uuid.UUID, data: ProductUpdate
) -> ProductOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    p = _get(db, org_id, pid, lock=True)
    if p.archived_at is not None:
        raise AppError(409, "product_archived", "Архивный товар нельзя изменять.")
    before = _snapshot(_outs(db, [p])[0])
    sent = data.model_fields_set

    new_unit = data.unit_id if "unit_id" in sent and data.unit_id else p.unit_id
    if new_unit != p.unit_id:
        _unit(db, org_id, new_unit)
        # Складские количества хранятся в единице товара: смена единицы при наличии движений
        # молча изменила бы смысл остатков (таблица stock_movement читается напрямую, чтобы не
        # нарушать слои модулей).
        if db.execute(
            text(
                "SELECT 1 FROM stock_movement WHERE organization_id = :o AND product_id = :p LIMIT 1"
            ),
            {"o": org_id, "p": p.id},
        ).first():
            raise _field_error(
                409,
                "product_unit_locked",
                "unit_id",
                "Единицу нельзя изменить: по товару уже есть складские движения.",
            )
    if "price" in sent or "price_currency" in sent:
        price = data.price if "price" in sent else p.price
        currency = data.price_currency if "price_currency" in sent else p.price_currency
        if (price is None) != (currency is None):
            raise _field_error(
                422,
                "price_currency_required" if price is not None else "price_required",
                "price_currency",
                "Цена и валюта задаются вместе.",
            )
        _check_price(db, org_id, price, currency)
        p.price, p.price_currency = price, currency
    if "sku" in sent and data.sku and data.sku != str(p.sku):
        _sku_taken(db, org_id, data.sku, p.id)
        p.sku = data.sku
    new_barcodes: list[str] | None = data.barcodes if "barcodes" in sent else None
    if new_barcodes is not None:
        _barcode_conflicts(db, org_id, new_barcodes, p.id)
    for field in ("kind", "name"):
        value = getattr(data, field)
        if field in sent and value is not None:
            setattr(p, field, value)
    for field in ("ikpu", "description"):
        if field in sent:
            setattr(p, field, getattr(data, field))
    p.unit_id = new_unit

    if new_barcodes is not None:
        have = {
            str(v).lower(): i
            for i, v in db.execute(
                select(ProductIdentifier.id, ProductIdentifier.value).where(
                    ProductIdentifier.product_id == p.id
                )
            )
        }
        keep = {c.lower() for c in new_barcodes}
        stale = [i for v, i in have.items() if v not in keep]
        if stale:
            db.execute(delete(ProductIdentifier).where(ProductIdentifier.id.in_(stale)))
        for code in new_barcodes:
            if code.lower() not in have:
                db.add(
                    ProductIdentifier(
                        id=new_id(),
                        organization_id=org_id,
                        product_id=p.id,
                        kind="barcode",
                        value=code,
                    )
                )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "product_sku_taken", "Артикул или штрихкод уже используется.") from exc
    db.refresh(p)
    out = _outs(db, [p])[0]
    after = _snapshot(out)
    if after != before:
        audit.record(
            db,
            action="product.updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="product",
            object_id=p.id,
            before=before,
            after=after,
        )
    db.commit()
    return out


def set_archived(
    db: Session, principal: Principal, org_id: uuid.UUID, pid: uuid.UUID, archived: bool
) -> ProductOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    p = _get(db, org_id, pid, lock=True)
    if (p.archived_at is not None) == archived:
        return _outs(db, [p])[0]
    p.archived_at = utcnow() if archived else None
    audit.record(
        db,
        action="product.archived" if archived else "product.restored",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="product",
        object_id=p.id,
        after={"sku": str(p.sku), "name": p.name},
    )
    db.commit()
    return _outs(db, [p])[0]


def count_active_by_unit(db: Session, org_id: uuid.UUID, unit_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Product)
            .where(
                Product.organization_id == org_id,
                Product.unit_id == unit_id,
                Product.archived_at.is_(None),
            )
        )
        or 0
    )


def count_active_by_currency(db: Session, org_id: uuid.UUID, code: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Product)
            .where(
                Product.organization_id == org_id,
                Product.price_currency == code,
                Product.archived_at.is_(None),
            )
        )
        or 0
    )
