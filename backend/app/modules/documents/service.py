"""Use cases: документы. Срез 3.1 — загрузка, ручная проверка и утверждение; распознавания пока нет."""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.catalog.models import Counterparty, Product, UnitOfMeasure
from app.modules.documents import file_checks, validation
from app.modules.documents.extraction.view import build_view
from app.modules.documents.models import Document, DocumentFile, DocumentLine
from app.modules.documents.schemas import (
    CounterpartyRef,
    DocumentListItem,
    DocumentOut,
    DocumentUpdate,
    FileOut,
    IssueOut,
    LineIn,
    LineOut,
)
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import OrgAccess, authorize
from app.modules.tenancy.models import Currency, OrganizationCurrency
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, like_pattern, paginate
from app.platform.storage import get_storage

EDITABLE_STATUS = "NEEDS_REVIEW"
SORTABLE: dict[str, SQLColumnExpression[Any]] = {
    "created_at": Document.created_at,
    "doc_date": Document.doc_date,
    "number": Document.number,
}
STATUS_FILTERS = {
    "needs_review": ("NEEDS_REVIEW",),
    "approved": ("APPROVED",),
    "rejected": ("REJECTED",),
    "duplicate": ("DUPLICATE",),
    "archived": ("ARCHIVED",),
}


def _fail(
    http: int, code: str, message: str, field: str | None = None, /, **params: Any
) -> AppError:
    details = [{"field": field or "", "code": code, "message": message, "params": params}]
    return AppError(http, code, message, details)


# ---------- форматирование чисел (точные строки, без float) ----------


def fmt_money(v: Decimal | None, minor: int | None) -> str | None:
    if v is None:
        return None
    digits = minor if minor is not None else max(2, -v.normalize().as_tuple().exponent)  # type: ignore[operator]
    return str(v.quantize(Decimal(1).scaleb(-digits)))


def fmt_qty(v: Decimal | None) -> str | None:
    if v is None:
        return None
    s = format(v.normalize(), "f")
    return s


# ---------- загрузка ----------


def _existing_by_hash(db: Session, org_id: uuid.UUID, sha: str) -> Document | None:
    return db.scalar(
        select(Document)
        .join(DocumentFile, DocumentFile.document_id == Document.id)
        .where(DocumentFile.organization_id == org_id, DocumentFile.sha256 == sha)
    )


def _duplicate_error(doc: Document) -> AppError:
    return _fail(
        409,
        "document_duplicate",
        "Такой файл уже загружен.",
        "file",
        document_id=str(doc.id),
        status=doc.status,
        number=doc.number,
    )


def upload_document(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    filename: str,
    data: bytes,
    doc_type: str,
) -> DocumentOut:
    access = authorize(db, principal, org_id, rbac.DOCUMENTS_UPLOAD)
    name = file_checks.safe_filename(filename)
    info = file_checks.inspect_upload(data, name)
    sha = hashlib.sha256(data).hexdigest()

    existing = _existing_by_hash(db, org_id, sha)
    if existing is not None:
        audit.record(
            db,
            action="document.duplicate_upload",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="document",
            object_id=existing.id,
            after={"original_name": name, "sha256": sha},
        )
        db.commit()
        raise _duplicate_error(existing)

    key = f"{org_id}/{sha[:2]}/{sha}"  # по содержимому: повторная запись того же файла безвредна
    get_storage().put(key, data, info.content_type)

    doc = Document(
        id=new_id(),
        organization_id=org_id,
        doc_type=doc_type,
        status=EDITABLE_STATUS,
        created_by=principal.user.id,
    )
    db.add(doc)
    try:
        db.flush()
        db.add(
            DocumentFile(
                id=new_id(),
                organization_id=org_id,
                document_id=doc.id,
                sha256=sha,
                size_bytes=len(data),
                content_type=info.content_type,
                original_name=name,
                storage_key=key,
            )
        )
        db.flush()
    except IntegrityError as exc:  # параллельная загрузка того же файла
        db.rollback()
        authorize(db, principal, org_id, rbac.DOCUMENTS_UPLOAD)
        winner = _existing_by_hash(db, org_id, sha)
        if winner is None:
            raise
        raise _duplicate_error(winner) from exc
    audit.record(
        db,
        action="document.uploaded",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="document",
        object_id=doc.id,
        after={
            "doc_type": doc_type,
            "original_name": name,
            "content_type": info.content_type,
            "size_bytes": len(data),
            "sha256": sha,
        },
    )
    db.commit()
    return _load_out(db, access, doc)


# ---------- чтение ----------


def _get(db: Session, org_id: uuid.UUID, doc_id: uuid.UUID, *, lock: bool = False) -> Document:
    stmt = select(Document).where(Document.id == doc_id, Document.organization_id == org_id)
    doc = db.scalar(stmt.with_for_update() if lock else stmt)
    if doc is None:
        raise AppError(404, "not_found", "Документ не найден.")
    return doc


def _db_issues(
    db: Session,
    org_id: uuid.UUID,
    doc: Document,
    lines: list[DocumentLine],
    cp: Counterparty | None,
) -> list[validation.Issue]:
    out: list[validation.Issue] = []
    if cp is not None:
        if cp.archived_at is not None:
            out.append(validation.Issue("counterparty_archived", "error", "counterparty_id"))
        if not cp.tin:
            out.append(validation.Issue("counterparty_tin_missing", "warning", "counterparty_id"))
        if doc.number:
            dups = db.scalars(
                select(Document.id)
                .where(
                    Document.organization_id == org_id,
                    Document.id != doc.id,
                    Document.counterparty_id == cp.id,
                    func.lower(func.btrim(Document.number)) == doc.number.strip().lower(),
                    Document.status.notin_(("REJECTED", "ARCHIVED", "DUPLICATE")),
                )
                .limit(3)
            ).all()
            if dups:
                out.append(
                    validation.Issue(
                        "possible_duplicate",
                        "warning",
                        "number",
                        {"document_ids": [str(d) for d in dups]},
                    )
                )
    if doc.currency:
        enabled = db.scalar(
            select(OrganizationCurrency.enabled).where(
                OrganizationCurrency.organization_id == org_id,
                OrganizationCurrency.currency_code == doc.currency,
            )
        )
        if not enabled:
            out.append(validation.Issue("currency_not_enabled", "error", "currency"))
    unit_ids = {ln.unit_id for ln in lines if ln.unit_id}
    inactive = (
        set(
            db.scalars(
                select(UnitOfMeasure.id).where(
                    UnitOfMeasure.id.in_(unit_ids), UnitOfMeasure.is_active.is_(False)
                )
            )
        )
        if unit_ids
        else set()
    )
    product_ids = {ln.product_id for ln in lines if ln.product_id}
    archived_products = (
        set(
            db.scalars(
                select(Product.id).where(
                    Product.id.in_(product_ids), Product.archived_at.is_not(None)
                )
            )
        )
        if product_ids
        else set()
    )
    for ln in lines:
        if ln.unit_id in inactive:
            out.append(
                validation.Issue(
                    "line_unit_unavailable", "error", "lines", {"position": ln.position}
                )
            )
        if ln.product_id in archived_products:
            out.append(
                validation.Issue(
                    "line_product_archived", "warning", "lines", {"position": ln.position}
                )
            )
    return out


def compute_issues(
    db: Session,
    org_id: uuid.UUID,
    doc: Document,
    lines: list[DocumentLine],
    cp: Counterparty | None,
) -> list[validation.Issue]:
    minor = (
        db.scalar(select(Currency.minor_units).where(Currency.code == doc.currency))
        if doc.currency
        else None
    )
    header = validation.HeaderData(
        doc_type=doc.doc_type,
        number=doc.number,
        doc_date=doc.doc_date,
        has_counterparty=doc.counterparty_id is not None,
        currency=doc.currency,
        total_amount=doc.total_amount,
        tax_total=doc.tax_total,
    )
    data = [
        validation.LineData(
            position=ln.position,
            quantity=ln.quantity,
            unit_price=ln.unit_price,
            discount=ln.discount,
            tax_amount=ln.tax_amount,
            amount=ln.amount,
        )
        for ln in lines
    ]
    issues = validation.validate(header, data, minor_units=minor, today=date.today())
    return issues + _db_issues(db, org_id, doc, lines, cp)


def _lines(db: Session, doc_id: uuid.UUID) -> list[DocumentLine]:
    return list(
        db.scalars(
            select(DocumentLine)
            .where(DocumentLine.document_id == doc_id)
            .order_by(DocumentLine.position)
        )
    )


def _load_out(db: Session, access: OrgAccess, doc: Document) -> DocumentOut:
    org_id = access.org.id
    lines = _lines(db, doc.id)
    cp = db.get(Counterparty, doc.counterparty_id) if doc.counterparty_id else None
    f = db.scalar(select(DocumentFile).where(DocumentFile.document_id == doc.id))
    minor = (
        db.scalar(select(Currency.minor_units).where(Currency.code == doc.currency))
        if doc.currency
        else None
    )
    units = (
        {
            u.id: str(u.code)
            for u in db.scalars(
                select(UnitOfMeasure).where(
                    UnitOfMeasure.id.in_({ln.unit_id for ln in lines if ln.unit_id})
                )
            )
        }
        if lines
        else {}
    )
    skus = (
        {
            p.id: str(p.sku)
            for p in db.scalars(
                select(Product).where(
                    Product.id.in_({ln.product_id for ln in lines if ln.product_id})
                )
            )
        }
        if lines
        else {}
    )
    names = {
        u.id: u.full_name
        for u in db.scalars(
            select(User).where(User.id.in_({x for x in (doc.created_by, doc.approved_by) if x}))
        )
    }
    issues = compute_issues(db, org_id, doc, lines, cp)
    return DocumentOut(
        id=doc.id,
        doc_type=doc.doc_type,
        status=doc.status,
        number=doc.number,
        doc_date=doc.doc_date,
        counterparty=CounterpartyRef(
            id=cp.id, name=cp.name, tin=cp.tin, archived=cp.archived_at is not None
        )
        if cp
        else None,
        currency=doc.currency,
        total_amount=fmt_money(doc.total_amount, minor),
        tax_total=fmt_money(doc.tax_total, minor),
        notes=doc.notes,
        file=FileOut(
            original_name=f.original_name,
            content_type=f.content_type,
            size_bytes=f.size_bytes,
            sha256=f.sha256,
            inline=file_checks.KINDS[kind_of(f.content_type)][2],
        )
        if f
        else None,
        lines=[
            LineOut(
                position=ln.position,
                description=ln.description,
                product_id=ln.product_id,
                product_sku=skus.get(ln.product_id) if ln.product_id else None,
                unit_id=ln.unit_id,
                unit_code=units.get(ln.unit_id) if ln.unit_id else None,
                quantity=fmt_qty(ln.quantity),
                unit_price=fmt_money(ln.unit_price, None),
                discount=fmt_money(ln.discount, minor),
                tax_amount=fmt_money(ln.tax_amount, minor),
                amount=fmt_money(ln.amount, minor),
            )
            for ln in lines
        ],
        issues=[
            IssueOut(code=i.code, severity=i.severity, field=i.field, params=i.params)
            for i in issues
        ],
        version=doc.version,
        extraction=build_view(db, doc.id),
        duplicate_of_id=doc.duplicate_of_id,
        rejection_reason=doc.rejection_reason,
        approved_by_name=names.get(doc.approved_by) if doc.approved_by else None,
        approved_at=doc.approved_at,
        created_by_name=names.get(doc.created_by),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def kind_of(content_type: str) -> str:
    return next(k for k, (ct, _e, _i) in file_checks.KINDS.items() if ct == content_type)


def get_document(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID
) -> DocumentOut:
    access = authorize(db, principal, org_id, rbac.DOCUMENTS_READ)
    return _load_out(db, access, _get(db, org_id, doc_id))


def list_documents(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    status: str,
    doc_type: str | None,
    counterparty_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> Page[DocumentListItem]:
    authorize(db, principal, org_id, rbac.DOCUMENTS_READ)
    stmt = (
        select(Document)
        .outerjoin(DocumentFile, DocumentFile.document_id == Document.id)
        .outerjoin(
            Counterparty,
            (Counterparty.id == Document.counterparty_id)
            & (Counterparty.organization_id == Document.organization_id),
        )
        .where(Document.organization_id == org_id)
    )
    if status == "active":
        stmt = stmt.where(Document.status != "ARCHIVED")
    elif status in STATUS_FILTERS:
        stmt = stmt.where(Document.status.in_(STATUS_FILTERS[status]))
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if counterparty_id:
        stmt = stmt.where(Document.counterparty_id == counterparty_id)
    if date_from:
        stmt = stmt.where(Document.doc_date >= date_from)
    if date_to:
        stmt = stmt.where(Document.doc_date <= date_to)
    if lq.q:
        pat = like_pattern(lq.q)
        stmt = stmt.where(
            or_(
                Document.number.ilike(pat, escape="\\"),
                DocumentFile.original_name.ilike(pat, escape="\\"),
                Counterparty.name.ilike(pat, escape="\\"),
            )
        )
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map=SORTABLE,
        default_sort="created_at",
        id_column=Document.id,
        default_order="desc",
    )
    ids = [d.id for d in rows]
    files = (
        {
            f.document_id: f
            for f in db.scalars(select(DocumentFile).where(DocumentFile.document_id.in_(ids)))
        }
        if ids
        else {}
    )
    cps = (
        {
            c.id: c.name
            for c in db.scalars(
                select(Counterparty).where(
                    Counterparty.id.in_({d.counterparty_id for d in rows if d.counterparty_id})
                )
            )
        }
        if rows
        else {}
    )
    minors = (
        dict(
            db.execute(
                select(Currency.code, Currency.minor_units).where(
                    Currency.code.in_({d.currency for d in rows if d.currency})
                )
            ).all()
        )
        if rows
        else {}
    )
    items = [
        DocumentListItem(
            id=d.id,
            doc_type=d.doc_type,
            status=d.status,
            number=d.number,
            doc_date=d.doc_date,
            counterparty_name=cps.get(d.counterparty_id) if d.counterparty_id else None,
            currency=d.currency,
            total_amount=fmt_money(d.total_amount, minors.get(d.currency or "")),
            original_name=files[d.id].original_name if d.id in files else None,
            version=d.version,
            created_at=d.created_at,
        )
        for d in rows
    ]
    return Page[DocumentListItem](items=items, total=total, page=lq.page, page_size=lq.page_size)


def open_file(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID
) -> tuple[DocumentFile, Iterator[bytes]]:
    authorize(db, principal, org_id, rbac.DOCUMENTS_READ)
    doc = _get(db, org_id, doc_id)
    f = db.scalar(select(DocumentFile).where(DocumentFile.document_id == doc.id))
    if f is None:
        raise AppError(404, "not_found", "Файл не найден.")
    audit.record(
        db,
        action="document.file_downloaded",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="document",
        object_id=doc.id,
        after={"original_name": f.original_name},
    )
    db.commit()
    return f, get_storage().stream(f.storage_key)


# ---------- редактирование ----------


def _snapshot(doc: Document, lines: list[DocumentLine]) -> dict[str, Any]:
    def s(v: Any) -> Any:
        return str(v) if isinstance(v, Decimal | date | uuid.UUID) else v

    return {
        "doc_type": doc.doc_type,
        "number": doc.number,
        "doc_date": s(doc.doc_date),
        "counterparty_id": s(doc.counterparty_id),
        "currency": doc.currency,
        "total_amount": s(doc.total_amount),
        "tax_total": s(doc.tax_total),
        "notes": doc.notes,
        "lines": [
            {
                "position": ln.position,
                "description": ln.description,
                "product_id": s(ln.product_id),
                "unit_id": s(ln.unit_id),
                "quantity": s(ln.quantity),
                "unit_price": s(ln.unit_price),
                "discount": s(ln.discount),
                "tax_amount": s(ln.tax_amount),
                "amount": s(ln.amount),
            }
            for ln in lines
        ],
    }


def _check_decimals(minor: int, values: dict[str, Decimal | None]) -> None:
    for field, v in values.items():
        if v is not None and -v.normalize().as_tuple().exponent > minor:  # type: ignore[operator]
            raise _fail(
                422, "too_many_decimals", "Слишком много знаков после запятой.", field, max=minor
            )


def update_document(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID, data: DocumentUpdate
) -> DocumentOut:
    access = authorize(db, principal, org_id, rbac.DOCUMENTS_REVIEW)
    doc = _get(db, org_id, doc_id, lock=True)
    if doc.status != EDITABLE_STATUS:
        raise _fail(409, "document_not_editable", "Документ в этом статусе изменять нельзя.")
    if doc.version != data.expected_version:
        raise _fail(
            409, "document_conflict", "Документ изменён другим пользователем.", version=doc.version
        )

    old_lines = _lines(db, doc.id)
    before = _snapshot(doc, old_lines)
    sent = data.model_fields_set

    if "counterparty_id" in sent and data.counterparty_id != doc.counterparty_id:
        if data.counterparty_id is not None:
            cp = db.scalar(
                select(Counterparty).where(
                    Counterparty.id == data.counterparty_id, Counterparty.organization_id == org_id
                )
            )
            if cp is None or cp.archived_at is not None:
                raise _fail(
                    422, "counterparty_unavailable", "Контрагент недоступен.", "counterparty_id"
                )
        doc.counterparty_id = data.counterparty_id
    if "currency" in sent and data.currency != doc.currency:
        if data.currency is not None:
            if db.get(Currency, data.currency) is None:
                raise _fail(422, "currency_unknown", "Неизвестная валюта.", "currency")
            enabled = db.scalar(
                select(OrganizationCurrency.enabled).where(
                    OrganizationCurrency.organization_id == org_id,
                    OrganizationCurrency.currency_code == data.currency,
                )
            )
            if not enabled:
                raise _fail(
                    422, "currency_not_enabled", "Валюта не включена в организации.", "currency"
                )
        doc.currency = data.currency
    for f in ("doc_type", "number", "doc_date", "total_amount", "tax_total", "notes"):
        if f in sent:
            v = getattr(data, f)
            if f == "doc_type" and v is None:
                continue
            setattr(doc, f, v)

    new_lines: list[LineIn] | None = data.lines if "lines" in sent else None
    if new_lines is not None:
        unit_ids = {ln.unit_id for ln in new_lines if ln.unit_id}
        if unit_ids:
            ok = set(
                db.scalars(
                    select(UnitOfMeasure.id).where(
                        UnitOfMeasure.id.in_(unit_ids),
                        or_(
                            UnitOfMeasure.organization_id.is_(None),
                            UnitOfMeasure.organization_id == org_id,
                        ),
                    )
                )
            )
            if unit_ids - ok:
                raise _fail(422, "unit_unavailable", "Единица измерения недоступна.", "lines")
        product_ids = {ln.product_id for ln in new_lines if ln.product_id}
        if product_ids:
            okp = set(
                db.scalars(
                    select(Product.id).where(
                        Product.id.in_(product_ids), Product.organization_id == org_id
                    )
                )
            )
            if product_ids - okp:
                raise _fail(422, "product_unavailable", "Товар недоступен.", "lines")

    minor = (
        db.scalar(select(Currency.minor_units).where(Currency.code == doc.currency))
        if doc.currency
        else None
    )
    if minor is not None:
        _check_decimals(minor, {"total_amount": doc.total_amount, "tax_total": doc.tax_total})
        for i, ln in enumerate(new_lines or [], 1):
            _check_decimals(
                minor,
                {
                    f"lines.{i}.discount": ln.discount,
                    f"lines.{i}.tax_amount": ln.tax_amount,
                    f"lines.{i}.amount": ln.amount,
                },
            )

    if new_lines is not None:
        db.execute(delete(DocumentLine).where(DocumentLine.document_id == doc.id))
        db.flush()
        for pos, ln in enumerate(new_lines, 1):
            db.add(
                DocumentLine(
                    id=new_id(),
                    organization_id=org_id,
                    document_id=doc.id,
                    position=pos,
                    description=ln.description,
                    product_id=ln.product_id,
                    unit_id=ln.unit_id,
                    quantity=ln.quantity,
                    unit_price=ln.unit_price,
                    discount=ln.discount,
                    tax_amount=ln.tax_amount,
                    amount=ln.amount,
                )
            )
    db.flush()
    after = _snapshot(doc, _lines(db, doc.id))
    if after != before:
        doc.version += 1
        audit.record(
            db,
            action="document.updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="document",
            object_id=doc.id,
            before=before,
            after=after,
        )
    db.commit()
    db.refresh(doc)
    return _load_out(db, access, doc)


# ---------- переходы статусов ----------


def _lock_for_action(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    permission: str,
    expected_version: int,
) -> tuple[OrgAccess, Document]:
    access = authorize(db, principal, org_id, permission)
    doc = _get(db, org_id, doc_id, lock=True)
    if doc.version != expected_version:
        raise _fail(
            409, "document_conflict", "Документ изменён другим пользователем.", version=doc.version
        )
    return access, doc


def _bad_transition(doc: Document) -> AppError:
    return _fail(
        409,
        "invalid_status_transition",
        "Это действие недоступно в текущем статусе.",
        status=doc.status,
    )


def _finish(
    db: Session,
    principal: Principal,
    access: OrgAccess,
    doc: Document,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> DocumentOut:
    doc.version += 1
    audit.record(
        db,
        action=action,
        actor_user_id=principal.user.id,
        organization_id=access.org.id,
        object_type="document",
        object_id=doc.id,
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(doc)
    return _load_out(db, access, doc)


def approve(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    expected_version: int,
    acknowledged: list[str],
) -> DocumentOut:
    access, doc = _lock_for_action(
        db, principal, org_id, doc_id, rbac.DOCUMENTS_APPROVE, expected_version
    )
    if doc.status != "NEEDS_REVIEW":
        raise _bad_transition(doc)
    cp = db.get(Counterparty, doc.counterparty_id) if doc.counterparty_id else None
    issues = compute_issues(db, org_id, doc, _lines(db, doc.id), cp)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise AppError(
            409,
            "document_has_errors",
            "В документе есть ошибки, утверждение невозможно.",
            [
                {"field": i.field or "", "code": i.code, "message": i.code, "params": i.params}
                for i in errors
            ],
        )
    unack = [i for i in issues if i.severity == "warning" and i.code not in set(acknowledged)]
    if unack:
        raise AppError(
            409,
            "warnings_unacknowledged",
            "Подтвердите предупреждения, чтобы утвердить документ.",
            [
                {"field": i.field or "", "code": i.code, "message": i.code, "params": i.params}
                for i in unack
            ],
        )
    before = {"status": doc.status}
    doc.status, doc.approved_by, doc.approved_at = "APPROVED", principal.user.id, utcnow()
    return _finish(
        db,
        principal,
        access,
        doc,
        "document.approved",
        before,
        {
            "status": doc.status,
            "acknowledged": sorted({i.code for i in issues if i.severity == "warning"}),
        },
    )


def reject(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    expected_version: int,
    reason: str,
) -> DocumentOut:
    access, doc = _lock_for_action(
        db, principal, org_id, doc_id, rbac.DOCUMENTS_REVIEW, expected_version
    )
    if doc.status != "NEEDS_REVIEW":
        raise _bad_transition(doc)
    before = {"status": doc.status}
    doc.status, doc.rejection_reason = "REJECTED", reason
    return _finish(
        db,
        principal,
        access,
        doc,
        "document.rejected",
        before,
        {"status": doc.status, "reason": reason},
    )


def mark_duplicate(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    expected_version: int,
    original_id: uuid.UUID,
) -> DocumentOut:
    access, doc = _lock_for_action(
        db, principal, org_id, doc_id, rbac.DOCUMENTS_REVIEW, expected_version
    )
    if doc.status != "NEEDS_REVIEW":
        raise _bad_transition(doc)
    original = db.scalar(
        select(Document).where(Document.id == original_id, Document.organization_id == org_id)
    )
    if original is None or original.id == doc.id or original.status == "DUPLICATE":
        raise _fail(
            422,
            "duplicate_original_invalid",
            "Укажите другой документ организации как оригинал.",
            "original_id",
        )
    before = {"status": doc.status}
    doc.status, doc.duplicate_of_id = "DUPLICATE", original.id
    return _finish(
        db,
        principal,
        access,
        doc,
        "document.marked_duplicate",
        before,
        {"status": doc.status, "duplicate_of_id": str(original.id)},
    )


def reopen(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID, expected_version: int
) -> DocumentOut:
    # снять утверждение вправе только утверждающий; остальные возвраты — проверяющий.
    # Сначала доступ к документам (он же ставит контекст организации), потом чтение статуса.
    authorize(db, principal, org_id, rbac.DOCUMENTS_READ)
    peek = _get(db, org_id, doc_id)
    perm = rbac.DOCUMENTS_APPROVE if peek.status == "APPROVED" else rbac.DOCUMENTS_REVIEW
    access, doc = _lock_for_action(db, principal, org_id, doc_id, perm, expected_version)
    if doc.status not in ("APPROVED", "REJECTED", "DUPLICATE"):
        raise _bad_transition(doc)
    before = {"status": doc.status}
    doc.status = EDITABLE_STATUS
    doc.approved_by = doc.approved_at = doc.rejection_reason = doc.duplicate_of_id = None
    return _finish(db, principal, access, doc, "document.reopened", before, {"status": doc.status})


def archive(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID, expected_version: int
) -> DocumentOut:
    authorize(db, principal, org_id, rbac.DOCUMENTS_READ)
    peek = _get(db, org_id, doc_id)
    perm = rbac.DOCUMENTS_APPROVE if peek.status == "APPROVED" else rbac.DOCUMENTS_REVIEW
    access, doc = _lock_for_action(db, principal, org_id, doc_id, perm, expected_version)
    if doc.status not in ("NEEDS_REVIEW", "APPROVED", "REJECTED", "DUPLICATE"):
        raise _bad_transition(doc)
    before = {
        "status": doc.status,
        "reason": doc.rejection_reason,
        "duplicate_of_id": str(doc.duplicate_of_id) if doc.duplicate_of_id else None,
    }
    doc.status = "ARCHIVED"
    doc.approved_by = doc.approved_at = doc.rejection_reason = doc.duplicate_of_id = None
    return _finish(db, principal, access, doc, "document.archived", before, {"status": doc.status})


def restore(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID, expected_version: int
) -> DocumentOut:
    access, doc = _lock_for_action(
        db, principal, org_id, doc_id, rbac.DOCUMENTS_REVIEW, expected_version
    )
    if doc.status != "ARCHIVED":
        raise _bad_transition(doc)
    before = {"status": doc.status}
    doc.status = EDITABLE_STATUS
    return _finish(db, principal, access, doc, "document.restored", before, {"status": doc.status})
