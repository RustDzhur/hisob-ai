"""Use cases: бухгалтерия (срез 5.1) — движок без встроенных правил.

В системе нет предзаполненных счетов, шаблонов проводок, налоговых ставок: их вводит бухгалтер.
Движок обеспечивает только инварианты: дебет = кредит (и на уровне БД), неизменяемость проведённого
(исправление — сторно), закрытый период нельзя менять, сквозная нумерация, аудит, права, Decimal.
Курсов и пересчёта валют нет: проводка и отчёты — в одной валюте.
"""

import calendar
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.modules.accounting.models import (
    Account,
    AccountingCounter,
    AccountingPeriod,
    JournalEntry,
    JournalLine,
    PostingRule,
)
from app.modules.accounting.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    EntryCreate,
    EntryListItem,
    EntryOut,
    EntryReverse,
    LedgerOut,
    LedgerRow,
    LineOut,
    PeriodCreate,
    PeriodOut,
    RuleRef,
    SourceDoc,
    TrialBalanceOut,
    TrialRow,
)
from app.modules.audit import service as audit
from app.modules.documents.models import Document
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import Currency, OrganizationCurrency
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

ZERO = Decimal(0)


def _fail(
    http: int, code: str, message: str, field: str | None = None, /, **params: Any
) -> AppError:
    return AppError(
        http,
        code,
        message,
        [{"field": field or "", "code": code, "message": message, "params": params}],
    )


def money(d: Decimal, minor: int) -> str:
    """Точная строка суммы с числом знаков валюты (без округления: суммы уже проверены на знаки)."""
    return format(d.quantize(Decimal(1).scaleb(-minor)), "f")


def _minor(db: Session, currency: str) -> int:
    m = db.scalar(select(Currency.minor_units).where(Currency.code == currency))
    return int(m) if m is not None else 2


# ---------------- план счетов ----------------

SORTABLE_ACC: dict[str, SQLColumnExpression[Any]] = {"code": Account.code, "name": Account.name}


def _acc_out(db: Session, rows: list[Account]) -> list[AccountOut]:
    ids = [a.id for a in rows]
    used = {
        r[0]
        for r in db.execute(
            select(JournalLine.account_id).where(JournalLine.account_id.in_(ids)).distinct()
        )
    }
    parents = {
        a.id: a.code
        for a in db.scalars(
            select(Account).where(Account.id.in_({a.parent_id for a in rows if a.parent_id}))
        )
    }
    return [
        AccountOut(
            id=a.id,
            code=a.code,
            name=a.name,
            parent_id=a.parent_id,
            parent_code=parents.get(a.parent_id) if a.parent_id else None,
            allow_posting=a.allow_posting,
            notes=a.notes,
            archived=a.archived_at is not None,
            has_entries=a.id in used,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in rows
    ]


def _get_account(db: Session, org_id: uuid.UUID, aid: uuid.UUID, *, lock: bool = False) -> Account:
    stmt = select(Account).where(Account.id == aid, Account.organization_id == org_id)
    a = db.scalar(stmt.with_for_update() if lock else stmt)
    if a is None:
        raise AppError(404, "not_found", "Счёт не найден.")
    return a


def list_accounts(
    db: Session, principal: Principal, org_id: uuid.UUID, lq: ListQuery, *, status: str
) -> Page[AccountOut]:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    stmt = select(Account).where(Account.organization_id == org_id)
    if status == "active":
        stmt = stmt.where(Account.archived_at.is_(None))
    elif status == "archived":
        stmt = stmt.where(Account.archived_at.is_not(None))
    stmt = search(stmt, lq.q, Account.code, Account.name)
    rows, total = paginate(
        db, stmt, lq, sort_map=SORTABLE_ACC, default_sort="code", id_column=Account.id
    )
    return Page[AccountOut](
        items=_acc_out(db, rows), total=total, page=lq.page, page_size=lq.page_size
    )


def _check_parent(
    db: Session, org_id: uuid.UUID, parent_id: uuid.UUID, self_id: uuid.UUID | None
) -> None:
    parent = db.scalar(
        select(Account).where(Account.id == parent_id, Account.organization_id == org_id)
    )
    if parent is None:
        raise _fail(422, "parent_not_found", "Родительский счёт не найден.", "parent_id")
    if parent.archived_at is not None:
        raise _fail(422, "parent_archived", "Родительский счёт в архиве.", "parent_id")
    cur: Account | None = parent
    depth = 0
    while cur is not None:  # защита от циклов
        if self_id is not None and cur.id == self_id:
            raise _fail(
                422, "parent_cycle", "Счёт не может быть вложен в самого себя.", "parent_id"
            )
        cur = db.get(Account, cur.parent_id) if cur.parent_id else None
        depth += 1
        if depth > 20:
            raise _fail(422, "parent_too_deep", "Слишком глубокая вложенность счетов.", "parent_id")


def _code_taken(db: Session, org_id: uuid.UUID, code: str, exclude: uuid.UUID | None) -> bool:
    other = db.scalar(
        select(Account.id).where(Account.organization_id == org_id, Account.code == code)
    )
    return other is not None and other != exclude


def create_account(
    db: Session, principal: Principal, org_id: uuid.UUID, data: AccountCreate
) -> AccountOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    if _code_taken(db, org_id, data.code, None):
        raise _fail(409, "account_code_taken", "Счёт с таким кодом уже существует.", "code")
    if data.parent_id:
        _check_parent(db, org_id, data.parent_id, None)
    a = Account(id=new_id(), organization_id=org_id, **data.model_dump())
    db.add(a)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409, "account_code_taken", "Счёт с таким кодом уже существует.", "code"
        ) from exc
    audit.record(
        db,
        action="account.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="account",
        object_id=a.id,
        after={"code": a.code, "name": a.name},
    )
    db.commit()
    return _acc_out(db, [a])[0]


def update_account(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID, data: AccountUpdate
) -> AccountOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    a = _get_account(db, org_id, aid, lock=True)
    if a.archived_at is not None:
        raise AppError(409, "account_archived", "Архивный счёт нельзя изменять.")
    sent = data.model_fields_set
    before = {
        "code": a.code,
        "name": a.name,
        "parent_id": str(a.parent_id) if a.parent_id else None,
        "allow_posting": a.allow_posting,
    }
    if (
        "code" in sent
        and data.code
        and data.code != a.code
        and _code_taken(db, org_id, data.code, a.id)
    ):
        raise _fail(409, "account_code_taken", "Счёт с таким кодом уже существует.", "code")
    if "parent_id" in sent and data.parent_id:
        _check_parent(db, org_id, data.parent_id, a.id)
    if "code" in sent and data.code:
        a.code = data.code
    if "name" in sent and data.name:
        a.name = data.name
    if "parent_id" in sent:
        a.parent_id = data.parent_id
    if "allow_posting" in sent and data.allow_posting is not None:
        a.allow_posting = data.allow_posting
    if "notes" in sent:
        a.notes = data.notes
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409, "account_code_taken", "Счёт с таким кодом уже существует.", "code"
        ) from exc
    after = {
        "code": a.code,
        "name": a.name,
        "parent_id": str(a.parent_id) if a.parent_id else None,
        "allow_posting": a.allow_posting,
    }
    if after != before:
        audit.record(
            db,
            action="account.updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="account",
            object_id=a.id,
            before=before,
            after=after,
        )
    db.commit()
    return _acc_out(db, [a])[0]


def set_account_archived(
    db: Session, principal: Principal, org_id: uuid.UUID, aid: uuid.UUID, archived: bool
) -> AccountOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    a = _get_account(db, org_id, aid, lock=True)
    if (a.archived_at is not None) == archived:
        return _acc_out(db, [a])[0]
    if archived:
        child = db.scalar(
            select(Account.id)
            .where(
                Account.organization_id == org_id,
                Account.parent_id == a.id,
                Account.archived_at.is_(None),
            )
            .limit(1)
        )
        if child is not None:
            raise AppError(409, "account_has_children", "Сначала архивируйте вложенные счета.")
        a.archived_at = utcnow()
    else:
        if a.parent_id:
            parent = db.get(Account, a.parent_id)
            if parent is not None and parent.archived_at is not None:
                raise AppError(409, "parent_archived", "Родительский счёт в архиве.")
        a.archived_at = None
    audit.record(
        db,
        action="account.archived" if archived else "account.restored",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="account",
        object_id=a.id,
        after={"code": a.code},
    )
    db.commit()
    return _acc_out(db, [a])[0]


# ---------------- периоды ----------------


def _month_bounds(d: date) -> tuple[date, date]:
    return date(d.year, d.month, 1), date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _ensure_period(
    db: Session, org_id: uuid.UUID, d: date, *, lock_shared: bool = False
) -> AccountingPeriod:
    start, end = _month_bounds(d)
    db.execute(
        pg_insert(AccountingPeriod)
        .values(id=new_id(), organization_id=org_id, start_date=start, end_date=end, status="OPEN")
        .on_conflict_do_nothing(index_elements=["organization_id", "start_date"])
    )
    stmt = select(AccountingPeriod).where(
        AccountingPeriod.organization_id == org_id, AccountingPeriod.start_date == start
    )
    p = db.scalar(stmt.with_for_update(read=True) if lock_shared else stmt)
    assert p is not None
    return p


def _period_out(db: Session, rows: list[AccountingPeriod]) -> list[PeriodOut]:
    counts = {
        r[0]: int(r[1])
        for r in db.execute(
            select(JournalEntry.period_id, func.count())
            .where(JournalEntry.period_id.in_([p.id for p in rows]))
            .group_by(JournalEntry.period_id)
        )
    }
    return [
        PeriodOut(
            id=p.id,
            start_date=p.start_date,
            end_date=p.end_date,
            status=p.status,
            closed_at=p.closed_at,
            entries_count=counts.get(p.id, 0),
        )
        for p in rows
    ]


def list_periods(db: Session, principal: Principal, org_id: uuid.UUID) -> list[PeriodOut]:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    rows = list(
        db.scalars(
            select(AccountingPeriod)
            .where(AccountingPeriod.organization_id == org_id)
            .order_by(AccountingPeriod.start_date.desc())
        )
    )
    return _period_out(db, rows)


def create_period(
    db: Session, principal: Principal, org_id: uuid.UUID, data: PeriodCreate
) -> PeriodOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    p = _ensure_period(db, org_id, date(data.year, data.month, 1))
    db.commit()
    return _period_out(db, [p])[0]


def set_period_status(
    db: Session, principal: Principal, org_id: uuid.UUID, pid: uuid.UUID, *, close: bool
) -> PeriodOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    p = db.scalar(
        select(AccountingPeriod)
        .where(AccountingPeriod.id == pid, AccountingPeriod.organization_id == org_id)
        .with_for_update()  # ждёт завершения проводок, удерживающих период FOR SHARE
    )
    if p is None:
        raise AppError(404, "not_found", "Период не найден.")
    if (p.status == "CLOSED") == close:
        return _period_out(db, [p])[0]
    if close:
        p.status, p.closed_by, p.closed_at = "CLOSED", principal.user.id, utcnow()
    else:
        p.status, p.closed_by, p.closed_at = "OPEN", None, None
    audit.record(
        db,
        action="period.closed" if close else "period.reopened",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="accounting_period",
        object_id=p.id,
        after={"start_date": p.start_date.isoformat()},
    )
    db.commit()
    return _period_out(db, [p])[0]


# ---------------- проводки ----------------


def _next_number(db: Session, org_id: uuid.UUID) -> int:
    stmt = (
        pg_insert(AccountingCounter)
        .values(organization_id=org_id, last_number=1)
        .on_conflict_do_update(
            index_elements=["organization_id"],
            set_={"last_number": AccountingCounter.last_number + 1},
        )
        .returning(AccountingCounter.last_number)
    )
    return int(db.execute(stmt).scalar_one())


def _entry_out(db: Session, e: JournalEntry) -> EntryOut:
    minor = _minor(db, e.currency)
    rows = db.execute(
        select(JournalLine, Account)
        .join(
            Account,
            (Account.id == JournalLine.account_id)
            & (Account.organization_id == JournalLine.organization_id),
        )
        .where(JournalLine.entry_id == e.id)
        .order_by(JournalLine.position)
    ).all()
    name = db.scalar(select(User.full_name).where(User.id == e.created_by))
    rev_by = db.scalar(select(JournalEntry.id).where(JournalEntry.reverses_id == e.id))
    total = sum((ln.debit for ln, _ in rows), ZERO)
    doc = (
        db.scalar(select(Document).where(Document.id == e.source_id))
        if e.source_type == "document" and e.source_id
        else None
    )
    rule = db.get(PostingRule, e.rule_id) if e.rule_id else None
    return EntryOut(
        id=e.id,
        number=e.number,
        entry_date=e.entry_date,
        currency=e.currency,
        description=e.description,
        reverses_id=e.reverses_id,
        reversed_by_id=rev_by,
        period_id=e.period_id,
        created_by_name=name,
        created_at=e.created_at,
        total=money(total, minor),
        source_document=(
            SourceDoc(id=doc.id, number=doc.number, doc_type=doc.doc_type) if doc else None
        ),
        rule=(
            RuleRef(id=rule.id, key=rule.rule_key, version=rule.version, name=rule.name)
            if rule
            else None
        ),
        lines=[
            LineOut(
                position=ln.position,
                account_id=a.id,
                account_code=a.code,
                account_name=a.name,
                debit=money(ln.debit, minor),
                credit=money(ln.credit, minor),
                memo=ln.memo,
            )
            for ln, a in rows
        ],
    )


def _check_decimals(lines: list[tuple[Decimal, Decimal]], minor: int) -> None:
    for i, (d, c) in enumerate(lines, 1):
        for field, v in (("debit", d), ("credit", c)):
            if v != 0 and -v.normalize().as_tuple().exponent > minor:  # type: ignore[operator]
                raise _fail(
                    422,
                    "too_many_decimals",
                    "Слишком много знаков после запятой для валюты.",
                    f"lines.{i}.{field}",
                    minor_units=minor,
                )


def create_entry(
    db: Session, principal: Principal, org_id: uuid.UUID, data: EntryCreate
) -> EntryOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_POST)
    enabled = db.scalar(
        select(OrganizationCurrency.enabled).where(
            OrganizationCurrency.organization_id == org_id,
            OrganizationCurrency.currency_code == data.currency,
        )
    )
    if not enabled:
        raise _fail(422, "currency_not_enabled", "Валюта не включена в организации.", "currency")
    minor = _minor(db, data.currency)
    _check_decimals([(ln.debit, ln.credit) for ln in data.lines], minor)

    ids = {ln.account_id for ln in data.lines}
    accounts = {
        a.id: a
        for a in db.scalars(
            select(Account).where(Account.id.in_(ids), Account.organization_id == org_id)
        )
    }
    for i, ln in enumerate(data.lines, 1):
        a = accounts.get(ln.account_id)
        field = f"lines.{i}.account_id"
        if a is None:
            raise _fail(422, "account_not_found", "Счёт не найден.", field, position=i)
        if a.archived_at is not None:
            raise _fail(422, "account_archived", "Счёт в архиве.", field, position=i)
        if not a.allow_posting:
            raise _fail(
                422,
                "account_not_postable",
                "На групповой счёт проводки не делаются.",
                field,
                position=i,
            )
    debit = sum((ln.debit for ln in data.lines), ZERO)
    credit = sum((ln.credit for ln in data.lines), ZERO)
    if debit != credit:
        raise _fail(
            422,
            "entry_not_balanced",
            "Сумма дебета не равна сумме кредита.",
            "lines",
            debit=money(debit, minor),
            credit=money(credit, minor),
        )
    return _insert_entry(
        db,
        principal,
        org_id,
        data.entry_date,
        data.currency,
        data.description,
        [(ln.account_id, ln.debit, ln.credit, ln.memo) for ln in data.lines],
        reverses=None,
    )


def _insert_entry(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    entry_date: date,
    currency: str,
    description: str,
    lines: list[tuple[uuid.UUID, Decimal, Decimal, str | None]],
    *,
    reverses: uuid.UUID | None,
    source: tuple[str, uuid.UUID, uuid.UUID, int]
    | None = None,  # (тип, id источника, id правила, версия)
) -> EntryOut:
    period = _ensure_period(db, org_id, entry_date, lock_shared=True)
    if period.status != "OPEN":
        db.rollback()
        raise _fail(409, "period_closed", "Период закрыт: проводку сделать нельзя.", "entry_date")
    e = JournalEntry(
        id=new_id(),
        organization_id=org_id,
        number=_next_number(db, org_id),
        entry_date=entry_date,
        period_id=period.id,
        currency=currency,
        description=description,
        reverses_id=reverses,
        source_type=source[0] if source else None,
        source_id=source[1] if source else None,
        rule_id=source[2] if source else None,
        rule_version=source[3] if source else None,
        created_by=principal.user.id,
    )
    db.add(e)
    try:
        db.flush()
    except IntegrityError as exc:  # параллельное сторно той же проводки (UNIQUE reverses_id)
        db.rollback()
        raise _fail(409, "entry_already_reversed", "Проводка уже сторнирована.") from exc
    for i, (aid, d, c, memo) in enumerate(lines, 1):
        db.add(
            JournalLine(
                id=new_id(),
                organization_id=org_id,
                entry_id=e.id,
                position=i,
                account_id=aid,
                debit=d,
                credit=c,
                memo=memo,
            )
        )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(409, "entry_conflict", "Проводка уже оформлена (параллельный запрос).") from exc
    audit.record(
        db,
        action=(
            "journal_entry.reversed"
            if reverses
            else "journal_entry.created_from_rule"
            if source
            else "journal_entry.created"
        ),
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="journal_entry",
        object_id=e.id,
        after={
            "number": e.number,
            "entry_date": entry_date.isoformat(),
            "currency": currency,
            "lines": len(lines),
            "reverses_id": str(reverses) if reverses else None,
        },
    )
    try:
        db.commit()  # здесь срабатывает отложенная проверка «дебет = кредит» на уровне БД
    except IntegrityError as exc:
        db.rollback()
        raise _fail(409, "entry_conflict", "Проводка уже оформлена (параллельный запрос).") from exc
    return _entry_out(db, e)


def reverse_entry(
    db: Session, principal: Principal, org_id: uuid.UUID, eid: uuid.UUID, data: EntryReverse
) -> EntryOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_POST)
    # без FOR UPDATE: роли приложения не выдан UPDATE на журнал; двойное сторно исключает UNIQUE(reverses_id)
    orig = db.scalar(
        select(JournalEntry).where(JournalEntry.id == eid, JournalEntry.organization_id == org_id)
    )
    if orig is None:
        raise AppError(404, "not_found", "Проводка не найдена.")
    if orig.reverses_id is not None:
        raise AppError(409, "entry_is_reversal", "Сторно-проводку сторнировать нельзя.")
    if db.scalar(select(JournalEntry.id).where(JournalEntry.reverses_id == orig.id)) is not None:
        raise AppError(409, "entry_already_reversed", "Проводка уже сторнирована.")
    src = list(
        db.scalars(
            select(JournalLine)
            .where(JournalLine.entry_id == orig.id)
            .order_by(JournalLine.position)
        )
    )
    return _insert_entry(
        db,
        principal,
        org_id,
        data.entry_date or utcnow().date(),
        orig.currency,
        data.description or f"Сторно проводки № {orig.number}",
        [(ln.account_id, ln.credit, ln.debit, ln.memo) for ln in src],  # стороны меняются местами
        reverses=orig.id,
    )


SORTABLE_ENT: dict[str, SQLColumnExpression[Any]] = {
    "entry_date": JournalEntry.entry_date,
    "number": JournalEntry.number,
}


def list_entries(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    date_from: date | None,
    date_to: date | None,
    account_id: uuid.UUID | None,
    currency: str | None,
) -> Page[EntryListItem]:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    stmt: Select[Any] = select(JournalEntry).where(JournalEntry.organization_id == org_id)
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    if currency:
        stmt = stmt.where(JournalEntry.currency == currency.upper())
    if account_id:
        stmt = stmt.where(
            exists().where(
                JournalLine.entry_id == JournalEntry.id, JournalLine.account_id == account_id
            )
        )
    stmt = search(stmt, lq.q, JournalEntry.description)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map=SORTABLE_ENT,
        default_sort="entry_date",
        id_column=JournalEntry.id,
        default_order="desc",
    )
    ids = [r.id for r in rows]
    totals = {
        r[0]: r[1]
        for r in db.execute(
            select(JournalLine.entry_id, func.sum(JournalLine.debit))
            .where(JournalLine.entry_id.in_(ids))
            .group_by(JournalLine.entry_id)
        )
    }
    reversed_ids = {
        r[0]
        for r in db.execute(
            select(JournalEntry.reverses_id).where(JournalEntry.reverses_id.in_(ids))
        )
    }
    minors = {c: _minor(db, c) for c in {r.currency for r in rows}}
    return Page[EntryListItem](
        items=[
            EntryListItem(
                id=r.id,
                number=r.number,
                entry_date=r.entry_date,
                currency=r.currency,
                description=r.description,
                total=money(Decimal(totals.get(r.id) or 0), minors[r.currency]),
                is_reversal=r.reverses_id is not None,
                reversed=r.id in reversed_ids,
            )
            for r in rows
        ],
        total=total,
        page=lq.page,
        page_size=lq.page_size,
    )


def get_entry(db: Session, principal: Principal, org_id: uuid.UUID, eid: uuid.UUID) -> EntryOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    e = db.scalar(
        select(JournalEntry).where(JournalEntry.id == eid, JournalEntry.organization_id == org_id)
    )
    if e is None:
        raise AppError(404, "not_found", "Проводка не найдена.")
    return _entry_out(db, e)


# ---------------- отчёты ----------------


def _side(net: Decimal, minor: int) -> tuple[str, str]:
    """Сальдо -> (дебет, кредит): положительное — дебетовое, отрицательное — кредитовое."""
    if net >= 0:
        return money(net, minor), money(ZERO, minor)
    return money(ZERO, minor), money(-net, minor)


def _report_currency(db: Session, org_id: uuid.UUID, currency: str | None, base: str) -> str:
    cur = (currency or base).upper()
    if db.scalar(select(Currency.code).where(Currency.code == cur)) is None:
        raise _fail(422, "currency_unknown", "Неизвестная валюта.", "currency")
    return cur


def trial_balance(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    currency: str | None,
) -> TrialBalanceOut:
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    if date_to < date_from:
        raise _fail(422, "invalid_range", "Конец периода раньше начала.", "date_to")
    cur = _report_currency(db, org_id, currency, acc.org.base_currency)
    minor = _minor(db, cur)
    net = JournalLine.debit - JournalLine.credit
    opening = func.coalesce(func.sum(case((JournalEntry.entry_date < date_from, net), else_=0)), 0)
    t_deb = func.coalesce(
        func.sum(
            case(
                (
                    (JournalEntry.entry_date >= date_from) & (JournalEntry.entry_date <= date_to),
                    JournalLine.debit,
                ),
                else_=0,
            )
        ),
        0,
    )
    t_cre = func.coalesce(
        func.sum(
            case(
                (
                    (JournalEntry.entry_date >= date_from) & (JournalEntry.entry_date <= date_to),
                    JournalLine.credit,
                ),
                else_=0,
            )
        ),
        0,
    )
    rows = db.execute(
        select(Account, opening, t_deb, t_cre)
        .join(
            JournalLine,
            (JournalLine.account_id == Account.id)
            & (JournalLine.organization_id == Account.organization_id),
        )
        .join(
            JournalEntry,
            (JournalEntry.id == JournalLine.entry_id)
            & (JournalEntry.organization_id == JournalLine.organization_id),
        )
        .where(
            Account.organization_id == org_id,
            JournalEntry.currency == cur,
            JournalEntry.entry_date <= date_to,
        )
        .group_by(Account.id)
        .order_by(Account.code)
    ).all()
    out: list[TrialRow] = []
    sums = [ZERO] * 6  # опен Дт, опен Кт, обороты Дт, Кт, закр Дт, Кт
    for a, op_net, td, tc in rows:
        op_net, td, tc = Decimal(op_net), Decimal(td), Decimal(tc)
        if op_net == 0 and td == 0 and tc == 0:
            continue
        od, oc = _side(op_net, minor)
        cd, cc = _side(op_net + td - tc, minor)
        out.append(
            TrialRow(
                account_id=a.id,
                code=a.code,
                name=a.name,
                opening_debit=od,
                opening_credit=oc,
                turnover_debit=money(td, minor),
                turnover_credit=money(tc, minor),
                closing_debit=cd,
                closing_credit=cc,
            )
        )
        for i, v in enumerate((Decimal(od), Decimal(oc), td, tc, Decimal(cd), Decimal(cc))):
            sums[i] += v
    totals = None
    if out:
        totals = TrialRow(
            account_id=uuid.UUID(int=0),
            code="",
            name="",
            opening_debit=money(sums[0], minor),
            opening_credit=money(sums[1], minor),
            turnover_debit=money(sums[2], minor),
            turnover_credit=money(sums[3], minor),
            closing_debit=money(sums[4], minor),
            closing_credit=money(sums[5], minor),
        )
    return TrialBalanceOut(
        currency=cur,
        date_from=date_from,
        date_to=date_to,
        rows=out,
        totals=totals,
        balanced=sums[2] == sums[3],
    )


def ledger(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    aid: uuid.UUID,
    lq: ListQuery,
    *,
    date_from: date,
    date_to: date,
    currency: str | None,
) -> LedgerOut:
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    if date_to < date_from:
        raise _fail(422, "invalid_range", "Конец периода раньше начала.", "date_to")
    a = _get_account(db, org_id, aid)
    cur = _report_currency(db, org_id, currency, acc.org.base_currency)
    minor = _minor(db, cur)
    net = JournalLine.debit - JournalLine.credit
    base_where = (
        JournalLine.account_id == a.id,
        JournalLine.organization_id == org_id,
        JournalEntry.currency == cur,
    )
    join_on = (JournalEntry.id == JournalLine.entry_id) & (
        JournalEntry.organization_id == JournalLine.organization_id
    )
    opening = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(net), 0))
            .select_from(JournalLine)
            .join(JournalEntry, join_on)
            .where(*base_where, JournalEntry.entry_date < date_from)
        )
        or 0
    )
    running = (
        func.sum(net).over(
            order_by=(JournalEntry.entry_date, JournalEntry.number, JournalLine.position)
        )
    ).label("run")
    inner = (
        select(
            JournalEntry.id.label("eid"),
            JournalEntry.number.label("num"),
            JournalEntry.entry_date.label("d"),
            JournalEntry.description.label("descr"),
            JournalLine.memo.label("memo"),
            JournalLine.debit.label("dr"),
            JournalLine.credit.label("cr"),
            JournalLine.position.label("pos"),
            running,
        )
        .select_from(JournalLine)
        .join(JournalEntry, join_on)
        .where(
            *base_where, JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to
        )
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(inner)) or 0
    page_rows = db.execute(
        select(inner)
        .order_by(inner.c.d, inner.c.num, inner.c.pos)
        .limit(lq.page_size)
        .offset((lq.page - 1) * lq.page_size)
    ).all()
    rows: list[LedgerRow] = []
    for r in page_rows:
        bd, bc = _side(opening + Decimal(r.run), minor)
        rows.append(
            LedgerRow(
                entry_id=r.eid,
                number=r.num,
                entry_date=r.d,
                description=r.descr,
                memo=r.memo,
                debit=money(Decimal(r.dr), minor),
                credit=money(Decimal(r.cr), minor),
                balance_debit=bd,
                balance_credit=bc,
            )
        )
    od, oc = _side(opening, minor)
    return LedgerOut(
        account_id=a.id,
        code=a.code,
        name=a.name,
        currency=cur,
        date_from=date_from,
        date_to=date_to,
        opening_debit=od,
        opening_credit=oc,
        rows=rows,
        total=int(total),
    )
