"""Сверка банковских операций с документами (срез 6.2).

Система ПРЕДЛАГАЕТ кандидатов с объяснением причин (баллы по понятным признакам), решает человек:
подтвердить, отклонить предложение, разбить платёж на несколько документов, объединить несколько платежей
в один документ, отменить сопоставление, пропустить операцию. Бухгалтерских проводок сверка не создаёт.
Валюты не пересчитываются: сопоставляются только операция и документ одной валюты.
"""

import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.bank.models import (
    BankAccount,
    BankMatch,
    BankMatchRejection,
    BankTransaction,
)
from app.modules.bank.schemas import (
    DocBrief,
    DocCandidate,
    DocumentBankOut,
    IgnoreIn,
    MatchCreate,
    MatchOut,
    MergeCreate,
    NameRef,
    Reason,
    RejectCandidateIn,
    TransactionDetailOut,
    TxnBrief,
    TxnCandidate,
)
from app.modules.bank.service import _fail, _minor, qty
from app.modules.catalog.models import Counterparty
from app.modules.documents.extraction.parsing import jaccard, tokens
from app.modules.documents.models import Document
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.validators import fold_confusables

ZERO = Decimal(0)
# Веса признаков (понятные и объяснимые). Предложение показывается от порога.
W_NUMBER = 40
W_AMOUNT_EQUAL = 40
W_AMOUNT_PARTIAL = 10
W_TIN = 30
W_NAME = 15
W_DATE = 10
THRESHOLD = 40
NAME_SIMILARITY = 0.6
DATE_WINDOW_DAYS = 90
POOL = 2000


# ---------------- вспомогательное ----------------


def _norm_text(v: str | None) -> str:
    return re.sub(r"[^a-z0-9а-яё]", "", fold_confusables(v or ""))


def _txn(db: Session, org_id: uuid.UUID, tid: uuid.UUID, *, lock: bool = False) -> BankTransaction:
    stmt = select(BankTransaction).where(
        BankTransaction.id == tid, BankTransaction.organization_id == org_id
    )
    t = db.scalar(stmt.with_for_update() if lock else stmt)
    if t is None:
        raise AppError(404, "not_found", "Банковская операция не найдена.")
    return t


def _doc(db: Session, org_id: uuid.UUID, did: uuid.UUID, *, lock: bool = False) -> Document:
    stmt = select(Document).where(Document.id == did, Document.organization_id == org_id)
    d = db.scalar(stmt.with_for_update() if lock else stmt)
    if d is None:
        raise AppError(404, "not_found", "Документ не найден.")
    return d


def _txn_matched(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not ids:
        return {}
    return {
        r[0]: Decimal(r[1])
        for r in db.execute(
            select(BankMatch.transaction_id, func.sum(BankMatch.amount))
            .where(
                BankMatch.organization_id == org_id,
                BankMatch.transaction_id.in_(ids),
                BankMatch.status == "CONFIRMED",
            )
            .group_by(BankMatch.transaction_id)
        )
    }


def _doc_matched(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not ids:
        return {}
    return {
        r[0]: Decimal(r[1])
        for r in db.execute(
            select(BankMatch.document_id, func.sum(BankMatch.amount))
            .where(
                BankMatch.organization_id == org_id,
                BankMatch.document_id.in_(ids),
                BankMatch.status == "CONFIRMED",
            )
            .group_by(BankMatch.document_id)
        )
    }


def txn_remaining(t: BankTransaction, matched: Decimal) -> Decimal:
    return abs(t.amount) - matched


def _reasons_out(raw: dict[str, Any] | None) -> list[Reason]:
    items = (raw or {}).get("reasons", [])
    return [
        Reason(
            code=str(x.get("code")), params={k: str(v) for k, v in (x.get("params") or {}).items()}
        )
        for x in items
    ]


def _match_out(
    db: Session, rows: list[tuple[BankMatch, BankTransaction, Document]]
) -> list[MatchOut]:
    names = (
        {
            r.id: r.full_name
            for r in db.execute(
                select(User.id, User.full_name).where(
                    User.id.in_({m.created_by for m, _, _ in rows})
                )
            )
        }
        if rows
        else {}
    )
    minors: dict[str, int] = {}
    out: list[MatchOut] = []
    for m, t, d in rows:
        minors.setdefault(t.currency, _minor(db, t.currency))
        out.append(
            MatchOut(
                id=m.id,
                transaction_id=t.id,
                document_id=d.id,
                document_number=d.number,
                document_type=d.doc_type,
                txn_date=t.txn_date,
                amount=qty(m.amount, minors[t.currency]),
                currency=t.currency,
                status=m.status,
                note=m.note,
                reasons=_reasons_out(m.explanation),
                created_by_name=names.get(m.created_by),
                created_at=m.created_at,
                cancelled_at=m.cancelled_at,
            )
        )
    return out


def _matches_for(
    db: Session,
    org_id: uuid.UUID,
    *,
    txn_id: uuid.UUID | None = None,
    doc_id: uuid.UUID | None = None,
) -> list[MatchOut]:
    stmt = (
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
        .where(BankMatch.organization_id == org_id)
        .order_by(BankMatch.created_at.desc())
    )
    if txn_id:
        stmt = stmt.where(BankMatch.transaction_id == txn_id)
    if doc_id:
        stmt = stmt.where(BankMatch.document_id == doc_id)
    return _match_out(db, [(m, t, d) for m, t, d in db.execute(stmt).all()])


# ---------------- кандидаты ----------------


def _score(
    t: BankTransaction,
    t_remaining: Decimal,
    d: Document,
    d_remaining: Decimal,
    cp: Counterparty | None,
) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    reasons: list[dict[str, Any]] = []
    text = _norm_text(t.description) + " " + _norm_text(t.reference)
    num = _norm_text(d.number)
    if len(num) >= 3 and num in text:
        score += W_NUMBER
        reasons.append({"code": "number_in_purpose", "params": {"number": d.number or ""}})
    if t_remaining == d_remaining:
        score += W_AMOUNT_EQUAL
        reasons.append({"code": "amount_equal", "params": {}})
    elif 0 < t_remaining < d_remaining:
        score += W_AMOUNT_PARTIAL
        reasons.append(
            {"code": "amount_partial", "params": {"remaining": format(d_remaining, "f")}}
        )
    if cp is not None:
        if cp.tin and t.counterparty_tin and cp.tin == t.counterparty_tin:
            score += W_TIN
            reasons.append({"code": "counterparty_tin", "params": {"tin": cp.tin}})
        elif (
            t.counterparty_name
            and jaccard(tokens(cp.name), tokens(t.counterparty_name)) >= NAME_SIMILARITY
        ):
            score += W_NAME
            reasons.append({"code": "counterparty_name", "params": {"name": cp.name}})
    if d.doc_date is not None:
        delta = (t.txn_date - d.doc_date).days
        if 0 <= delta <= DATE_WINDOW_DAYS:
            score += W_DATE
            reasons.append({"code": "date_window", "params": {"days": str(delta)}})
    return score, reasons


def _doc_brief(d: Document, cp: Counterparty | None, remaining: Decimal, minor: int) -> DocBrief:
    return DocBrief(
        id=d.id,
        number=d.number,
        doc_type=d.doc_type,
        doc_date=d.doc_date,
        counterparty_name=cp.name if cp else None,
        total=qty(d.total_amount or ZERO, minor),
        remaining=qty(remaining, minor),
    )


def doc_candidates(
    db: Session, org_id: uuid.UUID, t: BankTransaction, t_remaining: Decimal
) -> list[DocCandidate]:
    if t_remaining <= 0:
        return []
    minor = _minor(db, t.currency)
    docs = db.scalars(
        select(Document)
        .where(
            Document.organization_id == org_id,
            Document.status == "APPROVED",
            Document.currency == t.currency,
            Document.total_amount.is_not(None),
        )
        .order_by(Document.doc_date.desc().nulls_last(), Document.created_at.desc())
        .limit(POOL)
    ).all()
    rejected = {
        r[0]
        for r in db.execute(
            select(BankMatchRejection.document_id).where(
                BankMatchRejection.organization_id == org_id,
                BankMatchRejection.transaction_id == t.id,
            )
        )
    }
    matched = _doc_matched(db, org_id, [d.id for d in docs])
    cps = (
        {
            c.id: c
            for c in db.scalars(
                select(Counterparty).where(
                    Counterparty.id.in_({d.counterparty_id for d in docs if d.counterparty_id})
                )
            )
        }
        if any(d.counterparty_id for d in docs)
        else {}
    )
    out: list[DocCandidate] = []
    for d in docs:
        if d.id in rejected:
            continue
        remaining = (d.total_amount or ZERO) - matched.get(d.id, ZERO)
        if remaining <= 0:
            continue
        cp = cps.get(d.counterparty_id) if d.counterparty_id else None
        score, reasons = _score(t, t_remaining, d, remaining, cp)
        if score < THRESHOLD:
            continue
        out.append(
            DocCandidate(
                document=_doc_brief(d, cp, remaining, minor),
                score=score,
                reasons=[
                    Reason(code=r["code"], params={k: str(v) for k, v in r["params"].items()})
                    for r in reasons
                ],
                suggested_amount=qty(min(t_remaining, remaining), minor),
            )
        )
    out.sort(key=lambda c: -c.score)
    return out[:10]


def txn_candidates(
    db: Session, org_id: uuid.UUID, d: Document, d_remaining: Decimal
) -> list[TxnCandidate]:
    if d_remaining <= 0 or d.currency is None:
        return []
    cp = db.get(Counterparty, d.counterparty_id) if d.counterparty_id else None
    txns = db.scalars(
        select(BankTransaction)
        .where(
            BankTransaction.organization_id == org_id,
            BankTransaction.currency == d.currency,
            BankTransaction.status == "UNMATCHED",
        )
        .order_by(BankTransaction.txn_date.desc())
        .limit(POOL)
    ).all()
    rejected = {
        r[0]
        for r in db.execute(
            select(BankMatchRejection.transaction_id).where(
                BankMatchRejection.organization_id == org_id, BankMatchRejection.document_id == d.id
            )
        )
    }
    matched = _txn_matched(db, org_id, [t.id for t in txns])
    accounts = {
        a.id: a.name
        for a in db.scalars(select(BankAccount).where(BankAccount.organization_id == org_id))
    }
    minor = _minor(db, d.currency)
    out: list[TxnCandidate] = []
    for t in txns:
        if t.id in rejected:
            continue
        rem = txn_remaining(t, matched.get(t.id, ZERO))
        if rem <= 0:
            continue
        score, reasons = _score(t, rem, d, d_remaining, cp)
        if score < THRESHOLD:
            continue
        out.append(
            TxnCandidate(
                transaction=TxnBrief(
                    id=t.id,
                    txn_date=t.txn_date,
                    amount=qty(t.amount, minor),
                    currency=t.currency,
                    description=t.description,
                    counterparty_name=t.counterparty_name,
                    remaining=qty(rem, minor),
                    bank_account_name=accounts.get(t.bank_account_id, ""),
                ),
                score=score,
                reasons=[
                    Reason(code=r["code"], params={k: str(v) for k, v in r["params"].items()})
                    for r in reasons
                ],
                suggested_amount=qty(min(rem, d_remaining), minor),
            )
        )
    out.sort(key=lambda c: -c.score)
    return out[:10]


# ---------------- чтение ----------------


def transaction_detail(
    db: Session, principal: Principal, org_id: uuid.UUID, tid: uuid.UUID
) -> TransactionDetailOut:
    authorize(db, principal, org_id, rbac.BANK_READ)
    t = _txn(db, org_id, tid)
    matched = _txn_matched(db, org_id, [t.id]).get(t.id, ZERO)
    minor = _minor(db, t.currency)
    acc = db.get(BankAccount, t.bank_account_id)
    cp = db.get(Counterparty, t.counterparty_id) if t.counterparty_id else None
    remaining = txn_remaining(t, matched)
    candidates = doc_candidates(db, org_id, t, remaining) if t.status == "UNMATCHED" else []
    return TransactionDetailOut(
        id=t.id,
        bank_account=NameRef(id=t.bank_account_id, name=acc.name if acc else ""),
        import_id=t.import_id,
        txn_date=t.txn_date,
        amount=qty(t.amount, minor),
        currency=t.currency,
        description=t.description,
        reference=t.reference,
        counterparty_name=t.counterparty_name,
        counterparty_tin=t.counterparty_tin,
        counterparty=NameRef(id=cp.id, name=cp.name) if cp else None,
        status=t.status,
        matched_amount=qty(matched, minor),
        remaining=qty(remaining, minor),
        matches=_matches_for(db, org_id, txn_id=t.id),
        candidates=candidates,
    )


def document_bank(
    db: Session, principal: Principal, org_id: uuid.UUID, did: uuid.UUID
) -> DocumentBankOut:
    acc = authorize(db, principal, org_id, rbac.BANK_READ)
    if rbac.DOCUMENTS_READ not in acc.permissions:
        raise AppError(403, "forbidden", "Недостаточно прав для этого действия.")
    d = _doc(db, org_id, did)
    matched = _doc_matched(db, org_id, [d.id]).get(d.id, ZERO)
    minor = _minor(db, d.currency) if d.currency else 2
    remaining = (d.total_amount - matched) if d.total_amount is not None else None
    cands = (
        txn_candidates(db, org_id, d, remaining)
        if remaining is not None and d.status == "APPROVED"
        else []
    )
    return DocumentBankOut(
        document_id=d.id,
        currency=d.currency,
        total=qty(d.total_amount, minor) if d.total_amount is not None else None,
        matched_total=qty(matched, minor),
        remaining=qty(remaining, minor) if remaining is not None else None,
        matches=_matches_for(db, org_id, doc_id=d.id),
        candidates=cands,
    )


# ---------------- сопоставление ----------------


def _create_matches(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    pairs: list[tuple[uuid.UUID, uuid.UUID, Decimal]],
    note: str | None,
) -> list[uuid.UUID]:
    """Единый путь подтверждения: блокировки в стабильном порядке, проверка лимитов, запись, статусы, аудит."""
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for tid, did, _ in pairs:
        if (tid, did) in seen:
            raise _fail(
                422,
                "duplicate_allocation",
                "Одна и та же пара «операция — документ» указана дважды.",
                "allocations",
            )
        seen.add((tid, did))
    txn_ids = sorted({p[0] for p in pairs}, key=str)
    doc_ids = sorted({p[1] for p in pairs}, key=str)
    txns = {i: _txn(db, org_id, i, lock=True) for i in txn_ids}
    docs = {i: _doc(db, org_id, i, lock=True) for i in doc_ids}
    t_matched = _txn_matched(db, org_id, txn_ids)
    d_matched = _doc_matched(db, org_id, doc_ids)
    t_used: dict[uuid.UUID, Decimal] = {}
    d_used: dict[uuid.UUID, Decimal] = {}
    rejected = {
        (r[0], r[1])
        for r in db.execute(
            select(BankMatchRejection.transaction_id, BankMatchRejection.document_id).where(
                BankMatchRejection.organization_id == org_id,
                BankMatchRejection.transaction_id.in_(txn_ids),
            )
        )
    }
    created: list[uuid.UUID] = []
    for i, (tid, did, amount) in enumerate(pairs, 1):
        t, d = txns[tid], docs[did]
        field = f"allocations.{i}.amount"
        if t.status == "IGNORED":
            raise _fail(
                409,
                "transaction_ignored",
                "Операция пропущена: сначала верните её в сверку.",
                field,
            )
        if d.status != "APPROVED":
            raise _fail(
                409,
                "document_not_approved",
                "Сопоставлять можно только с утверждённым документом.",
                field,
            )
        if d.total_amount is None or d.currency is None:
            raise _fail(
                422, "document_total_missing", "В документе не указан итог или валюта.", field
            )
        if d.currency != t.currency:
            raise _fail(
                422, "currency_mismatch", "Валюта документа отличается от валюты операции.", field
            )
        minor = _minor(db, t.currency)
        if -amount.normalize().as_tuple().exponent > minor:  # type: ignore[operator]
            raise _fail(
                422,
                "too_many_decimals",
                "Слишком много знаков после запятой для валюты.",
                field,
                minor_units=minor,
            )
        t_used[tid] = t_used.get(tid, ZERO) + amount
        d_used[did] = d_used.get(did, ZERO) + amount
        t_rem = txn_remaining(t, t_matched.get(tid, ZERO))
        d_rem = d.total_amount - d_matched.get(did, ZERO)
        if t_used[tid] > t_rem:
            raise _fail(
                409,
                "amount_exceeds_transaction",
                "Сумма сопоставлений больше нераспределённого остатка операции.",
                field,
                remaining=qty(t_rem, minor),
            )
        if d_used[did] > d_rem:
            raise _fail(
                409,
                "amount_exceeds_document",
                "Сумма сопоставлений больше неоплаченного остатка документа.",
                field,
                remaining=qty(d_rem, minor),
            )
        # объяснение сохраняется вместе с сопоставлением: почему оно выглядело подходящим
        cp = db.get(Counterparty, d.counterparty_id) if d.counterparty_id else None
        _, reasons = _score(t, t_rem, d, d_rem, cp)
        if (tid, did) in rejected:
            reasons.append({"code": "was_rejected_before", "params": {}})
        m = BankMatch(
            id=new_id(),
            organization_id=org_id,
            transaction_id=tid,
            document_id=did,
            amount=amount,
            explanation={"reasons": reasons},
            note=note,
            created_by=principal.user.id,
        )
        db.add(m)
        created.append(m.id)
        audit.record(
            db,
            action="bank_match.created",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="bank_match",
            object_id=m.id,
            after={
                "transaction_id": str(tid),
                "document_id": str(did),
                "amount": format(amount, "f"),
                "reasons": [r["code"] for r in reasons],
            },
        )
    for tid in txn_ids:
        t = txns[tid]
        if txn_remaining(t, t_matched.get(tid, ZERO)) - t_used.get(tid, ZERO) == 0:
            t.status = "MATCHED"
    try:
        db.commit()
    except (
        IntegrityError,
        DBAPIError,
    ) as exc:  # отложенные проверки БД: страховка от обхода приложения
        db.rollback()
        raise _fail(
            409,
            "match_conflict",
            "Сопоставление не сохранено: суммы изменились. Обновите страницу.",
        ) from exc
    return created


def confirm(
    db: Session, principal: Principal, org_id: uuid.UUID, tid: uuid.UUID, data: MatchCreate
) -> TransactionDetailOut:
    authorize(db, principal, org_id, rbac.BANK_RECONCILE)
    _create_matches(
        db, principal, org_id, [(tid, a.document_id, a.amount) for a in data.allocations], data.note
    )
    return transaction_detail(db, principal, org_id, tid)


def merge(
    db: Session, principal: Principal, org_id: uuid.UUID, data: MergeCreate
) -> DocumentBankOut:
    authorize(db, principal, org_id, rbac.BANK_RECONCILE)
    _create_matches(
        db,
        principal,
        org_id,
        [(a.transaction_id, data.document_id, a.amount) for a in data.allocations],
        data.note,
    )
    return document_bank(db, principal, org_id, data.document_id)


def cancel(
    db: Session, principal: Principal, org_id: uuid.UUID, mid: uuid.UUID
) -> TransactionDetailOut:
    authorize(db, principal, org_id, rbac.BANK_RECONCILE)
    m0 = db.scalar(
        select(BankMatch).where(BankMatch.id == mid, BankMatch.organization_id == org_id)
    )
    if m0 is None:
        raise AppError(404, "not_found", "Сопоставление не найдено.")
    t = _txn(db, org_id, m0.transaction_id, lock=True)
    m = db.scalar(
        select(BankMatch)
        .where(BankMatch.id == mid, BankMatch.organization_id == org_id)
        .with_for_update()
    )
    assert m is not None
    if m.status != "CONFIRMED":
        raise AppError(409, "match_already_cancelled", "Сопоставление уже отменено.")
    m.status, m.cancelled_by, m.cancelled_at = "CANCELLED", principal.user.id, utcnow()
    if t.status == "MATCHED":
        t.status = "UNMATCHED"
    audit.record(
        db,
        action="bank_match.cancelled",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_match",
        object_id=m.id,
        after={
            "transaction_id": str(m.transaction_id),
            "document_id": str(m.document_id),
            "amount": format(m.amount, "f"),
        },
    )
    db.commit()
    return transaction_detail(db, principal, org_id, t.id)


def reject_candidate(
    db: Session, principal: Principal, org_id: uuid.UUID, tid: uuid.UUID, data: RejectCandidateIn
) -> TransactionDetailOut:
    authorize(db, principal, org_id, rbac.BANK_RECONCILE)
    t = _txn(db, org_id, tid)
    _doc(db, org_id, data.document_id)
    db.add(
        BankMatchRejection(
            id=new_id(),
            organization_id=org_id,
            transaction_id=t.id,
            document_id=data.document_id,
            rejected_by=principal.user.id,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # уже отклонено — идемпотентно
        return transaction_detail(db, principal, org_id, tid)
    audit.record(
        db,
        action="bank_match.candidate_rejected",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_transaction",
        object_id=t.id,
        after={"document_id": str(data.document_id)},
    )
    db.commit()
    return transaction_detail(db, principal, org_id, tid)


def set_ignored(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    tid: uuid.UUID,
    data: IgnoreIn,
    ignored: bool,
) -> TransactionDetailOut:
    authorize(db, principal, org_id, rbac.BANK_RECONCILE)
    t = _txn(db, org_id, tid, lock=True)
    if ignored:
        if t.status == "IGNORED":
            return transaction_detail(db, principal, org_id, tid)
        if _txn_matched(db, org_id, [t.id]).get(t.id, ZERO) > 0:
            raise AppError(
                409,
                "transaction_has_matches",
                "У операции есть сопоставления: сначала отмените их.",
            )
        t.status = "IGNORED"
    else:
        if t.status != "IGNORED":
            return transaction_detail(db, principal, org_id, tid)
        t.status = "UNMATCHED"
    audit.record(
        db,
        action="bank_transaction.ignored" if ignored else "bank_transaction.unignored",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="bank_transaction",
        object_id=t.id,
        after={"reason": data.reason},
    )
    db.commit()
    return transaction_detail(db, principal, org_id, tid)
