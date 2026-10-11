"""Отчёты по банку (срез 6.3): сводка по счетам, подтверждённые сопоставления, CSV.

Остатков банка (по выписке) система не знает: «входящее/исходящее» здесь — накопленная сумма
импортированных операций, а не сальдо счёта в банке.
"""

import csv
import io
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.bank.models import BankAccount, BankMatch, BankTransaction
from app.modules.bank.schemas import (
    MatchReportOut,
    MatchReportRow,
    NameRef,
    SummaryOut,
    SummaryRow,
)
from app.modules.bank.service import _fail, _minor, qty
from app.modules.catalog.models import Counterparty
from app.modules.documents.models import Document
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize

ZERO = Decimal(0)
MATCH_LIMIT = 1000
NOTE = (
    "Суммы считаются по импортированным операциям; это не остаток по выписке банка. "
    "«Не сопоставлено» — неразнесённая часть операций, кроме пропущенных."
)


def _check(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise _fail(422, "invalid_range", "Конец периода раньше начала.", "date_to")


def _matched_sq(org_id: uuid.UUID) -> Any:
    return (
        select(
            BankMatch.transaction_id.label("tid"),
            func.coalesce(func.sum(BankMatch.amount), ZERO).label("m"),
        )
        .where(BankMatch.organization_id == org_id, BankMatch.status == "CONFIRMED")
        .group_by(BankMatch.transaction_id)
        .subquery()
    )


def summary(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    account_id: uuid.UUID | None,
) -> SummaryOut:
    authorize(db, principal, org_id, rbac.BANK_READ)
    _check(date_from, date_to)
    T = BankTransaction
    ms = _matched_sq(org_id)
    in_p = (T.txn_date >= date_from) & (T.txn_date <= date_to)
    amt = T.amount

    def s(expr: Any) -> Any:
        return func.coalesce(func.sum(expr), ZERO)

    def n(cond: Any) -> Any:
        return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    stmt = (
        select(
            T.bank_account_id,
            T.currency,
            s(case((T.txn_date < date_from, amt), else_=ZERO)).label("opening"),
            s(case((in_p & (amt > 0), amt), else_=ZERO)).label("incoming"),
            s(case((in_p & (amt < 0), -amt), else_=ZERO)).label("outgoing"),
            n(in_p).label("cnt"),
            n(in_p & (T.status == "UNMATCHED")).label("un"),
            n(in_p & (T.status == "MATCHED")).label("ma"),
            n(in_p & (T.status == "IGNORED")).label("ig"),
            s(
                case(
                    (
                        in_p & (T.status != "IGNORED"),
                        func.abs(amt) - func.coalesce(ms.c.m, ZERO),
                    ),
                    else_=ZERO,
                )
            ).label("unm"),
        )
        .select_from(T)
        .outerjoin(ms, ms.c.tid == T.id)
        .where(T.organization_id == org_id, T.txn_date <= date_to)
        .group_by(T.bank_account_id, T.currency)
    )
    if account_id:
        stmt = stmt.where(T.bank_account_id == account_id)
    names = {
        a.id: a.name
        for a in db.scalars(select(BankAccount).where(BankAccount.organization_id == org_id))
    }
    rows: list[SummaryRow] = []
    for r in db.execute(stmt):
        minor = _minor(db, r.currency)
        opening, inc, out = Decimal(r.opening), Decimal(r.incoming), Decimal(r.outgoing)
        net = inc - out
        rows.append(
            SummaryRow(
                bank_account=NameRef(id=r.bank_account_id, name=names.get(r.bank_account_id, "")),
                currency=r.currency,
                opening=qty(opening, minor),
                incoming=qty(inc, minor),
                outgoing=qty(out, minor),
                net=qty(net, minor),
                closing=qty(opening + net, minor),
                txn_count=int(r.cnt),
                unmatched_count=int(r.un),
                matched_count=int(r.ma),
                ignored_count=int(r.ig),
                unmatched_amount=qty(Decimal(r.unm), minor),
            )
        )
    rows.sort(key=lambda x: (x.bank_account.name, x.currency))
    return SummaryOut(date_from=date_from, date_to=date_to, rows=rows, note=NOTE)


def matches_report(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    account_id: uuid.UUID | None,
    limit: int = MATCH_LIMIT,
) -> MatchReportOut:
    authorize(db, principal, org_id, rbac.BANK_READ)
    _check(date_from, date_to)
    base = (
        select(BankMatch, BankTransaction, Document)
        .join(
            BankTransaction,
            (BankTransaction.id == BankMatch.transaction_id)
            & (BankTransaction.organization_id == BankMatch.organization_id),
        )
        .join(
            Document,
            (Document.id == BankMatch.document_id)
            & (Document.organization_id == BankMatch.organization_id),
        )
        .where(
            BankMatch.organization_id == org_id,
            BankMatch.status == "CONFIRMED",
            BankTransaction.txn_date >= date_from,
            BankTransaction.txn_date <= date_to,
        )
    )
    if account_id:
        base = base.where(BankTransaction.bank_account_id == account_id)
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    found = db.execute(
        base.order_by(BankTransaction.txn_date, BankMatch.created_at, BankMatch.id).limit(limit)
    ).all()
    accs = {
        a.id: a.name
        for a in db.scalars(select(BankAccount).where(BankAccount.organization_id == org_id))
    }
    users = {
        u.id: u.full_name
        for u in db.scalars(
            select(User).where(User.id.in_({m.created_by for m, _, _ in found if m.created_by}))
        )
    }
    cps = {
        c.id: c.name
        for c in db.scalars(
            select(Counterparty).where(
                Counterparty.id.in_({d.counterparty_id for _, _, d in found if d.counterparty_id})
            )
        )
    }
    rows = [
        MatchReportRow(
            match_id=m.id,
            transaction_id=t.id,
            document_id=d.id,
            txn_date=t.txn_date,
            bank_account_name=accs.get(t.bank_account_id, ""),
            currency=t.currency,
            amount=qty(m.amount, _minor(db, t.currency)),
            document_number=d.number,
            document_type=d.doc_type,
            counterparty_name=cps.get(d.counterparty_id) if d.counterparty_id else None,
            confirmed_by_name=users.get(m.created_by) if m.created_by else None,
            confirmed_at=m.created_at,
        )
        for m, t, d in found
    ]
    return MatchReportOut(
        date_from=date_from, date_to=date_to, rows=rows, total=total, truncated=total > len(rows)
    )


def _safe(v: str | None) -> str:
    """Защита от формул в таблицах: значения, начинающиеся с = + - @, получают апостроф."""
    v = v or ""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def _audit_export(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    report: str,
    *,
    date_from: date,
    date_to: date,
    account_id: uuid.UUID | None,
    rows: int,
) -> None:
    """Выгрузка данных — событие аудита (кто, что, за какой период, сколько строк); содержимое не пишется."""
    audit.record(
        db,
        action="export.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="report",
        after={
            "report": report,
            "format": "csv",
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "account_id": str(account_id) if account_id else None,
            "rows": rows,
        },
    )
    db.commit()


def summary_csv(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    account_id: uuid.UUID | None,
) -> str:
    r = summary(db, principal, org_id, date_from=date_from, date_to=date_to, account_id=account_id)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Банк: сводка {date_from.isoformat()} — {date_to.isoformat()}"])
    w.writerow(
        [
            "Счёт",
            "Валюта",
            "Накоплено до периода",
            "Поступления",
            "Списания",
            "Итого за период",
            "Накоплено на конец",
            "Операций",
            "Не сверено",
            "Сверено",
            "Пропущено",
            "Не сопоставлено (сумма)",
        ]
    )
    for x in r.rows:
        w.writerow(
            [
                _safe(x.bank_account.name),
                x.currency,
                x.opening,
                x.incoming,
                x.outgoing,
                x.net,
                x.closing,
                x.txn_count,
                x.unmatched_count,
                x.matched_count,
                x.ignored_count,
                x.unmatched_amount,
            ]
        )
    w.writerow([_safe(r.note)])
    _audit_export(
        db,
        principal,
        org_id,
        "bank_summary",
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        rows=len(r.rows),
    )
    return buf.getvalue()


def matches_csv(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    account_id: uuid.UUID | None,
) -> str:
    r = matches_report(
        db,
        principal,
        org_id,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        limit=100000,
    )
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Банк: сопоставления {date_from.isoformat()} — {date_to.isoformat()}"])
    w.writerow(
        [
            "Дата операции",
            "Счёт",
            "Валюта",
            "Сумма",
            "Документ",
            "Тип",
            "Контрагент",
            "Кто",
            "Когда",
        ]
    )
    for x in r.rows:
        w.writerow(
            [
                x.txn_date.isoformat(),
                _safe(x.bank_account_name),
                x.currency,
                x.amount,
                _safe(x.document_number),
                x.document_type,
                _safe(x.counterparty_name),
                _safe(x.confirmed_by_name),
                x.confirmed_at.isoformat(),
            ]
        )
    _audit_export(
        db,
        principal,
        org_id,
        "bank_matches",
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        rows=len(r.rows),
    )
    return buf.getvalue()


def transactions_csv(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    account_id: uuid.UUID | None,
    status: str,
) -> str:
    authorize(db, principal, org_id, rbac.BANK_READ)
    _check(date_from, date_to)
    ms = _matched_sq(org_id)
    stmt = (
        select(BankTransaction, func.coalesce(ms.c.m, ZERO))
        .outerjoin(ms, ms.c.tid == BankTransaction.id)
        .where(
            BankTransaction.organization_id == org_id,
            BankTransaction.txn_date >= date_from,
            BankTransaction.txn_date <= date_to,
        )
        .order_by(BankTransaction.txn_date, BankTransaction.row_number, BankTransaction.id)
    )
    if account_id:
        stmt = stmt.where(BankTransaction.bank_account_id == account_id)
    if status in ("UNMATCHED", "MATCHED", "IGNORED"):
        stmt = stmt.where(BankTransaction.status == status)
    accs = {
        a.id: a.name
        for a in db.scalars(select(BankAccount).where(BankAccount.organization_id == org_id))
    }
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Банк: операции {date_from.isoformat()} — {date_to.isoformat()}"])
    w.writerow(
        [
            "Дата",
            "Счёт",
            "Валюта",
            "Сумма",
            "Назначение",
            "Номер",
            "Контрагент",
            "ИНН",
            "Статус",
            "Сопоставлено",
            "Не сопоставлено",
        ]
    )
    count = 0
    for t, matched in db.execute(stmt):
        count += 1
        minor = _minor(db, t.currency)
        w.writerow(
            [
                t.txn_date.isoformat(),
                _safe(accs.get(t.bank_account_id)),
                t.currency,
                qty(t.amount, minor),
                _safe(t.description),
                _safe(t.reference),
                _safe(t.counterparty_name),
                _safe(t.counterparty_tin),
                t.status,
                qty(Decimal(matched), minor),
                qty(abs(t.amount) - Decimal(matched), minor),
            ]
        )
    _audit_export(
        db,
        principal,
        org_id,
        "bank_transactions",
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        rows=count,
    )
    return buf.getvalue()
