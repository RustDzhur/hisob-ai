"""Use cases: банк (срез 6.1) — счета, профили импорта, импорт выписок, операции."""

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.audit import service as audit
from app.modules.bank import parsing
from app.modules.bank.models import BankAccount, BankImport, BankMatch, BankTransaction
from app.modules.bank.parsing import ImportProfile, ParsedRow
from app.modules.bank.schemas import (
    BankAccountCreate,
    BankAccountOut,
    BankAccountUpdate,
    HeadersOut,
    ImportOut,
    NameRef,
    PreviewOut,
    PreviewRow,
    RowErrorOut,
    TransactionOut,
)
from app.modules.catalog.models import Counterparty
from app.modules.documents import file_checks
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import Currency, OrganizationCurrency
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, like_pattern, paginate, search
from app.platform.storage import get_storage

MAX_FILE = 5 * 1024 * 1024
SORTABLE: dict[str, SQLColumnExpression[Any]] = {
    "txn_date": BankTransaction.txn_date,
    "amount": BankTransaction.amount,
    "created_at": BankTransaction.created_at,
}


def _fail(
    http: int, code: str, message: str, field: str | None = None, /, **params: Any
) -> AppError:
    return AppError(
        http,
        code,
        message,
        [{"field": field or "", "code": code, "message": message, "params": params}],
    )


def qty(d: Decimal, minor: int = 2) -> str:
    return format(d.quantize(Decimal(1).scaleb(-minor)), "f")


# ---------------- счета ----------------


def _acc_out(db: Session, rows: list[BankAccount]) -> list[BankAccountOut]:
    counts = {
        r[0]: int(r[1])
        for r in db.execute(
            select(BankTransaction.bank_account_id, func.count())
            .where(BankTransaction.bank_account_id.in_([a.id for a in rows]))
            .group_by(BankTransaction.bank_account_id)
        )
    }
    return [
        BankAccountOut(
            id=a.id,
            name=a.name,
            bank_name=a.bank_name,
            account_number=a.account_number,
            currency=a.currency,
            notes=a.notes,
            has_profile=a.import_profile is not None,
            archived=a.archived_at is not None,
            transactions_count=counts.get(a.id, 0),
            created_at=a.created_at,
        )
        for a in rows
    ]


def _get_account(
    db: Session, org_id: uuid.UUID, aid: uuid.UUID, *, lock: bool = False
) -> BankAccount:
    stmt = select(BankAccount).where(BankAccount.id == aid, BankAccount.organization_id == org_id)
    a = db.scalar(stmt.with_for_update() if lock else stmt)
    if a is None:
        raise AppError(404, "not_found", "Банковский счёт не найден.")
    return a


def list_accounts(
    db: Session, principal: Principal, org_id: uuid.UUID, lq: ListQuery, *, status: str
) -> Page[BankAccountOut]:
    authorize(db, principal, org_id, rbac.BANK_READ)
    stmt = select(BankAccount).where(BankAccount.organization_id == org_id)
    if status == "active":
        stmt = stmt.where(BankAccount.archived_at.is_(None))
    elif status == "archived":
        stmt = stmt.where(BankAccount.archived_at.is_not(None))
    stmt = search(stmt, lq.q, BankAccount.name, BankAccount.bank_name, BankAccount.account_number)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map={"name": BankAccount.name, "created_at": BankAccount.created_at},
        default_sort="name",
        id_column=BankAccount.id,
    )
    return Page[BankAccountOut](
        items=_acc_out(db, rows), total=total, page=lq.page, page_size=lq.page_size
    )


def get_account(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID
) -> BankAccountOut:
    authorize(db, principal, org_id, rbac.BANK_READ)
    return _acc_out(db, [_get_account(db, org_id, aid)])[0]


def create_account(
    db: Session, principal: Principal, org_id: uuid.UUID, data: BankAccountCreate
) -> BankAccountOut:
    authorize(db, principal, org_id, rbac.BANK_MANAGE)
    if db.scalar(select(Currency.code).where(Currency.code == data.currency)) is None:
        raise _fail(422, "currency_unknown", "Неизвестная валюта.", "currency")
    enabled = db.scalar(
        select(OrganizationCurrency.enabled).where(
            OrganizationCurrency.organization_id == org_id,
            OrganizationCurrency.currency_code == data.currency,
        )
    )
    if not enabled:
        raise _fail(422, "currency_not_enabled", "Валюта не включена в организации.", "currency")
    a = BankAccount(id=new_id(), organization_id=org_id, **data.model_dump())
    db.add(a)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409, "bank_account_taken", "Счёт с таким номером уже есть.", "account_number"
        ) from exc
    audit.record(
        db,
        action="bank_account.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_account",
        object_id=a.id,
        after={"account_number": a.account_number, "currency": a.currency},
    )
    db.commit()
    return _acc_out(db, [a])[0]


def update_account(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID, data: BankAccountUpdate
) -> BankAccountOut:
    authorize(db, principal, org_id, rbac.BANK_MANAGE)
    a = _get_account(db, org_id, aid, lock=True)
    if a.archived_at is not None:
        raise AppError(409, "bank_account_archived", "Архивный счёт нельзя изменять.")
    sent = data.model_fields_set
    if "name" in sent and data.name:
        a.name = data.name
    if "bank_name" in sent:
        a.bank_name = data.bank_name
    if "notes" in sent:
        a.notes = data.notes
    audit.record(
        db,
        action="bank_account.updated",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_account",
        object_id=a.id,
        after={"name": a.name},
    )
    db.commit()
    return _acc_out(db, [a])[0]


def set_archived(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID, archived: bool
) -> BankAccountOut:
    authorize(db, principal, org_id, rbac.BANK_MANAGE)
    a = _get_account(db, org_id, aid, lock=True)
    if (a.archived_at is not None) != archived:
        a.archived_at = utcnow() if archived else None
        audit.record(
            db,
            action="bank_account.archived" if archived else "bank_account.restored",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="bank_account",
            object_id=a.id,
            after={"account_number": a.account_number},
        )
        db.commit()
    return _acc_out(db, [a])[0]


# ---------------- профиль ----------------


def get_profile(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID
) -> ImportProfile | None:
    authorize(db, principal, org_id, rbac.BANK_READ)
    a = _get_account(db, org_id, aid)
    return ImportProfile.model_validate(a.import_profile) if a.import_profile else None


def set_profile(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID, profile: ImportProfile
) -> ImportProfile:
    authorize(db, principal, org_id, rbac.BANK_MANAGE)
    a = _get_account(db, org_id, aid, lock=True)
    a.import_profile = profile.model_dump(mode="json")
    audit.record(
        db,
        action="bank_account.profile_set",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_account",
        object_id=a.id,
        after={"format": profile.format, "date_format": profile.date_format},
    )
    db.commit()
    return profile


# ---------------- разбор файла ----------------


def _check_file(data: bytes, filename: str) -> None:
    if len(data) > MAX_FILE:
        raise AppError(
            413,
            "file_too_large",
            "Файл выписки слишком большой.",
            [
                {
                    "field": "file",
                    "code": "file_too_large",
                    "message": "Файл выписки слишком большой.",
                    "params": {"max_mb": MAX_FILE // (1024 * 1024)},
                }
            ],
        )
    info = file_checks.inspect_upload(data, filename)
    if info.kind not in ("csv", "xlsx"):
        raise _fail(
            422, "statement_format_unsupported", "Выписка должна быть файлом CSV или XLSX.", "file"
        )


def _parse_profile(raw: str | None, stored: dict[str, Any] | None) -> ImportProfile:
    import json

    try:
        source: Any = json.loads(raw) if raw else stored
    except ValueError as exc:
        raise _fail(422, "profile_invalid", "Профиль импорта не разобран.", "profile") from exc
    if not source:
        raise _fail(
            422,
            "profile_required",
            "Сначала задайте профиль импорта (сопоставление колонок).",
            "profile",
        )
    try:
        return ImportProfile.model_validate(source)
    except ValidationError as exc:
        code = str(exc.errors()[0].get("msg", "profile_invalid")).replace("Value error, ", "")
        raise _fail(
            422,
            code if code.startswith("profile_") else "profile_invalid",
            "Профиль импорта неверен.",
            "profile",
        ) from exc


def _minor(db: Session, currency: str) -> int:
    m = db.scalar(select(Currency.minor_units).where(Currency.code == currency))
    return int(m) if m is not None else 2


def _run_parse(
    db: Session, a: BankAccount, data: bytes, profile: ImportProfile
) -> parsing.ParseResult:
    try:
        return parsing.parse_statement(
            data, profile, currency=a.currency, minor_units=_minor(db, a.currency)
        )
    except parsing.ParseError as exc:
        raise _fail(422, exc.code, exc.message, "file") from exc


def headers(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    aid: uuid.UUID,
    data: bytes,
    filename: str,
    raw_profile: str,
) -> HeadersOut:
    """Заголовки и первые строки файла — чтобы пользователь сопоставил колонки."""
    authorize(db, principal, org_id, rbac.BANK_MANAGE)
    _get_account(db, org_id, aid)
    _check_file(data, filename)
    import json

    try:
        base = json.loads(raw_profile) if raw_profile else {}
    except ValueError as exc:
        raise _fail(422, "profile_invalid", "Параметры файла не разобраны.", "profile") from exc
    try:
        p = ImportProfile.model_validate(
            {"date_column": "-", "amount_column": "-", **base, "format": base.get("format", "csv")}
        )
        h, body = parsing.read_table(data, p)
    except ValidationError as exc:
        raise _fail(422, "profile_invalid", "Параметры файла неверны.", "profile") from exc
    except parsing.ParseError as exc:
        raise _fail(422, exc.code, exc.message, "file") from exc
    names = [x for x in h]
    return HeadersOut(
        headers=[x for x in names if x],
        sample=[
            {names[i]: (r[i] if i < len(r) else "") for i in range(len(names)) if names[i]}
            for _, r in body[:5]
        ],
    )


def _existing_hashes(db: Session, org_id: uuid.UUID, aid: uuid.UUID, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(hashes), 1000):
        chunk = hashes[i : i + 1000]
        found |= {
            r[0]
            for r in db.execute(
                select(BankTransaction.row_hash).where(
                    BankTransaction.organization_id == org_id,
                    BankTransaction.bank_account_id == aid,
                    BankTransaction.row_hash.in_(chunk),
                )
            )
        }
    return found


def preview(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    aid: uuid.UUID,
    data: bytes,
    filename: str,
    raw_profile: str | None,
) -> PreviewOut:
    authorize(db, principal, org_id, rbac.BANK_IMPORT)
    a = _get_account(db, org_id, aid)
    _check_file(data, filename)
    profile = _parse_profile(raw_profile, a.import_profile)
    res = _run_parse(db, a, data, profile)
    existing = _existing_hashes(db, org_id, aid, [r.row_hash for r in res.rows])
    sha = hashlib.sha256(data).hexdigest()
    already = db.scalar(
        select(func.count())
        .select_from(BankImport)
        .where(
            BankImport.organization_id == org_id,
            BankImport.bank_account_id == aid,
            BankImport.sha256 == sha,
        )
    )
    minor = _minor(db, a.currency)
    dup = sum(1 for r in res.rows if r.row_hash in existing)
    return PreviewOut(
        total=res.total,
        valid=len(res.rows),
        errors_count=len(res.errors),
        duplicates=dup,
        would_import=len(res.rows) - dup,
        errors=[RowErrorOut(row_number=e.row_number, code=e.code) for e in res.errors[:50]],
        sample=[
            PreviewRow(
                row_number=r.row_number,
                txn_date=r.txn_date,
                amount=qty(r.amount, minor),
                description=r.description,
                reference=r.reference,
                counterparty_name=r.counterparty_name,
                counterparty_tin=r.counterparty_tin,
                duplicate=r.row_hash in existing,
            )
            for r in res.rows[:50]
        ],
        already_imported=bool(already),
    )


# ---------------- импорт ----------------


def _import_out(db: Session, i: BankImport) -> ImportOut:
    name = db.scalar(select(User.full_name).where(User.id == i.created_by))
    return ImportOut(
        id=i.id,
        bank_account_id=i.bank_account_id,
        filename=i.filename,
        rows_total=i.rows_total,
        rows_imported=i.rows_imported,
        rows_duplicate=i.rows_duplicate,
        rows_error=i.rows_error,
        created_by_name=name,
        created_at=i.created_at,
    )


def import_statement(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    aid: uuid.UUID,
    data: bytes,
    filename: str,
    raw_profile: str | None,
    skip_errors: bool,
) -> ImportOut:
    authorize(db, principal, org_id, rbac.BANK_IMPORT)
    a = _get_account(db, org_id, aid, lock=True)
    if a.archived_at is not None:
        raise AppError(409, "bank_account_archived", "В архивный счёт выписки не загружаются.")
    _check_file(data, filename)
    sha = hashlib.sha256(data).hexdigest()
    prior = db.scalar(
        select(BankImport.id).where(
            BankImport.organization_id == org_id,
            BankImport.bank_account_id == aid,
            BankImport.sha256 == sha,
        )
    )
    if prior is not None:
        raise AppError(
            409,
            "statement_already_imported",
            "Этот файл уже загружен по этому счёту.",
            [
                {
                    "field": "file",
                    "code": "statement_already_imported",
                    "message": "Этот файл уже загружен.",
                    "params": {"import_id": str(prior)},
                }
            ],
        )
    profile = _parse_profile(raw_profile, a.import_profile)
    res = _run_parse(db, a, data, profile)
    if res.errors and not skip_errors:
        raise AppError(
            422,
            "statement_has_errors",
            "В выписке есть строки с ошибками: исправьте файл или загрузите с пропуском.",
            [
                {
                    "field": "file",
                    "code": e.code,
                    "message": e.code,
                    "params": {"row_number": e.row_number},
                }
                for e in res.errors[:50]
            ],
        )
    if not res.rows:
        raise _fail(422, "no_valid_rows", "В файле нет строк для загрузки.", "file")

    storage_key = f"bank/{org_id}/{sha[:2]}/{sha}"
    get_storage().put(storage_key, data, "application/octet-stream")
    imp = BankImport(
        id=new_id(),
        organization_id=org_id,
        bank_account_id=aid,
        filename=file_checks.safe_filename(filename),
        sha256=sha,
        size_bytes=len(data),
        storage_key=storage_key,
        rows_total=res.total,
        rows_imported=0,
        rows_duplicate=0,
        rows_error=len(res.errors),
        created_by=principal.user.id,
    )
    db.add(imp)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409, "statement_already_imported", "Этот файл уже загружен по этому счёту.", "file"
        ) from exc

    tins = {r.counterparty_tin for r in res.rows if r.counterparty_tin}
    cps = (
        {
            c.tin: c.id
            for c in db.scalars(
                select(Counterparty).where(
                    Counterparty.organization_id == org_id, Counterparty.tin.in_(tins)
                )
            )
        }
        if tins
        else {}
    )
    inserted = 0
    for i in range(0, len(res.rows), 500):
        chunk = res.rows[i : i + 500]
        values = [_row_values(org_id, aid, imp.id, a.currency, r, cps) for r in chunk]
        stmt = (
            pg_insert(BankTransaction)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=["organization_id", "bank_account_id", "row_hash"]
            )
            .returning(BankTransaction.id)
        )
        inserted += len(db.execute(stmt).all())
    imp.rows_imported = inserted
    imp.rows_duplicate = len(res.rows) - inserted
    audit.record(
        db,
        action="bank_import.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_import",
        object_id=imp.id,
        after={
            "account_id": str(aid),
            "rows_total": res.total,
            "imported": inserted,
            "duplicates": len(res.rows) - inserted,
            "errors": len(res.errors),
        },
    )
    db.commit()
    return _import_out(db, imp)


def _row_values(
    org_id: uuid.UUID,
    aid: uuid.UUID,
    import_id: uuid.UUID,
    currency: str,
    r: ParsedRow,
    cps: dict[str | None, uuid.UUID],
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "organization_id": org_id,
        "bank_account_id": aid,
        "import_id": import_id,
        "row_number": r.row_number,
        "txn_date": r.txn_date,
        "amount": r.amount,
        "currency": currency,
        "description": r.description,
        "reference": r.reference,
        "counterparty_name": r.counterparty_name,
        "counterparty_tin": r.counterparty_tin,
        "counterparty_id": cps.get(r.counterparty_tin) if r.counterparty_tin else None,
        "row_hash": r.row_hash,
        "status": "UNMATCHED",
        "raw": r.raw,
    }


def list_imports(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID
) -> list[ImportOut]:
    authorize(db, principal, org_id, rbac.BANK_READ)
    _get_account(db, org_id, aid)
    rows = db.scalars(
        select(BankImport)
        .where(BankImport.organization_id == org_id, BankImport.bank_account_id == aid)
        .order_by(BankImport.created_at.desc())
        .limit(100)
    ).all()
    return [_import_out(db, i) for i in rows]


# ---------------- операции ----------------


def list_transactions(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    account_id: uuid.UUID | None,
    status: str,
    direction: str,
    date_from: date | None,
    date_to: date | None,
    import_id: uuid.UUID | None = None,
) -> Page[TransactionOut]:
    authorize(db, principal, org_id, rbac.BANK_READ)
    stmt: Select[Any] = select(BankTransaction).where(BankTransaction.organization_id == org_id)
    if account_id:
        stmt = stmt.where(BankTransaction.bank_account_id == account_id)
    if import_id:
        stmt = stmt.where(BankTransaction.import_id == import_id)
    if status in ("UNMATCHED", "MATCHED", "IGNORED"):
        stmt = stmt.where(BankTransaction.status == status)
    if direction == "in":
        stmt = stmt.where(BankTransaction.amount > 0)
    elif direction == "out":
        stmt = stmt.where(BankTransaction.amount < 0)
    if date_from:
        stmt = stmt.where(BankTransaction.txn_date >= date_from)
    if date_to:
        stmt = stmt.where(BankTransaction.txn_date <= date_to)
    if lq.q:
        pat = like_pattern(lq.q)
        stmt = stmt.where(
            or_(
                BankTransaction.description.ilike(pat, escape="\\"),
                BankTransaction.reference.ilike(pat, escape="\\"),
                BankTransaction.counterparty_name.ilike(pat, escape="\\"),
                BankTransaction.counterparty_tin.ilike(pat, escape="\\"),
            )
        )
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map=SORTABLE,
        default_sort="txn_date",
        id_column=BankTransaction.id,
        default_order="desc",
    )
    accounts = {
        a.id: a
        for a in db.scalars(select(BankAccount).where(BankAccount.organization_id == org_id))
    }
    cps = (
        {
            c.id: c.name
            for c in db.scalars(
                select(Counterparty).where(
                    Counterparty.id.in_({r.counterparty_id for r in rows if r.counterparty_id})
                )
            )
        }
        if any(r.counterparty_id for r in rows)
        else {}
    )
    minors = {c: _minor(db, c) for c in {r.currency for r in rows}}
    matched = (
        {
            m[0]: Decimal(m[1])
            for m in db.execute(
                select(BankMatch.transaction_id, func.sum(BankMatch.amount))
                .where(
                    BankMatch.organization_id == org_id,
                    BankMatch.transaction_id.in_([r.id for r in rows]),
                    BankMatch.status == "CONFIRMED",
                )
                .group_by(BankMatch.transaction_id)
            )
        }
        if rows
        else {}
    )
    return Page[TransactionOut](
        items=[
            TransactionOut(
                id=r.id,
                bank_account=NameRef(id=r.bank_account_id, name=accounts[r.bank_account_id].name),
                import_id=r.import_id,
                txn_date=r.txn_date,
                amount=qty(r.amount, minors[r.currency]),
                currency=r.currency,
                description=r.description,
                reference=r.reference,
                counterparty_name=r.counterparty_name,
                counterparty_tin=r.counterparty_tin,
                counterparty=NameRef(id=r.counterparty_id, name=cps[r.counterparty_id])
                if r.counterparty_id and r.counterparty_id in cps
                else None,
                status=r.status,
                matched_amount=qty(matched.get(r.id, Decimal(0)), minors[r.currency]),
                remaining=qty(abs(r.amount) - matched.get(r.id, Decimal(0)), minors[r.currency]),
            )
            for r in rows
        ],
        total=total,
        page=lq.page,
        page_size=lq.page_size,
    )
