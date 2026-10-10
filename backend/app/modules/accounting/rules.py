"""Правила проводок (срез 5.2).

Встроенных правил нет: бухгалтер вводит правило (какие счета и стороны для какого типа документа),
проверяет и УТВЕРЖДАЕТ его с указанием основания. Утверждённое правило неизменяемо; изменение — новая версия.
По утверждённому документу система только ПРЕДЛАГАЕТ проводку (расчёт по правилу), проводит человек.
Суммы берутся из документа как напечатано: из полей «итого», «налог» и их разности. Ставки налогов и
округления в движке не заложены.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.accounting.models import Account, JournalEntry, PostingRule, PostingRuleLine
from app.modules.accounting.refdata import tax_mismatch
from app.modules.accounting.schemas import (
    EntryOut,
    FromDocumentIn,
    ProposalLine,
    ProposalOut,
    RuleApprove,
    RuleCreate,
    RuleLineIn,
    RuleLineOut,
    RuleListItem,
    RuleOut,
    RuleRef,
    RuleUpdate,
)
from app.modules.accounting.service import (
    ZERO,
    _fail,
    _insert_entry,
    _minor,
    money,
)
from app.modules.audit import service as audit
from app.modules.documents.models import DOC_TYPES, Document
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import OrganizationCurrency
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate, search

# Вклад источника суммы в (итого, налог): total_amount=(1,0), tax_total=(0,1), total_minus_tax=(1,-1).
COEFF: dict[str, tuple[int, int]] = {
    "total_amount": (1, 0),
    "tax_total": (0, 1),
    "total_minus_tax": (1, -1),
}


def _balanced(lines: list[RuleLineIn]) -> bool:
    """Правило сбалансировано символьно: для любых «итого» и «налог» сумма дебета равна сумме кредита."""
    d = [0, 0]
    c = [0, 0]
    for ln in lines:
        target = d if ln.side == "debit" else c
        target[0] += COEFF[ln.amount_source][0]
        target[1] += COEFF[ln.amount_source][1]
    return d == c and (d[0] != 0 or d[1] != 0)


def _get(db: Session, org_id: uuid.UUID, rid: uuid.UUID, *, lock: bool = False) -> PostingRule:
    stmt = select(PostingRule).where(PostingRule.id == rid, PostingRule.organization_id == org_id)
    r = db.scalar(stmt.with_for_update() if lock else stmt)
    if r is None:
        raise AppError(404, "not_found", "Правило не найдено.")
    return r


def _check_lines(db: Session, org_id: uuid.UUID, lines: list[RuleLineIn]) -> None:
    ids = {ln.account_id for ln in lines}
    accounts = {
        a.id: a
        for a in db.scalars(
            select(Account).where(Account.id.in_(ids), Account.organization_id == org_id)
        )
    }
    for i, ln in enumerate(lines, 1):
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
    if not _balanced(lines):
        raise _fail(
            422,
            "rule_not_balanced",
            "Правило не сбалансировано: суммы по дебету и кредиту должны совпадать при любых суммах документа.",
            "lines",
        )


def _check_header(doc_type: str, eff_from: date | None, eff_to: date | None) -> None:
    if doc_type not in DOC_TYPES:
        raise _fail(422, "doc_type_unknown", "Неизвестный тип документа.", "doc_type")
    if eff_from and eff_to and eff_to < eff_from:
        raise _fail(422, "invalid_range", "Конец действия раньше начала.", "effective_to")


def _out(db: Session, r: PostingRule) -> RuleOut:
    rows = db.execute(
        select(PostingRuleLine, Account)
        .join(
            Account,
            (Account.id == PostingRuleLine.account_id)
            & (Account.organization_id == PostingRuleLine.organization_id),
        )
        .where(PostingRuleLine.rule_id == r.id)
        .order_by(PostingRuleLine.position)
    ).all()
    name = (
        db.scalar(select(User.full_name).where(User.id == r.approved_by)) if r.approved_by else None
    )
    return RuleOut(
        id=r.id,
        rule_key=r.rule_key,
        version=r.version,
        name=r.name,
        description=r.description,
        doc_type=r.doc_type,
        status=r.status,
        legal_basis=r.legal_basis,
        effective_from=r.effective_from,
        effective_to=r.effective_to,
        approved_by_name=name,
        approved_at=r.approved_at,
        created_at=r.created_at,
        lines=[
            RuleLineOut(
                position=ln.position,
                account_id=a.id,
                account_code=a.code,
                account_name=a.name,
                side=ln.side,
                amount_source=ln.amount_source,
                memo=ln.memo,
            )
            for ln, a in rows
        ],
    )


def _write_lines(
    db: Session, org_id: uuid.UUID, rule_id: uuid.UUID, lines: list[RuleLineIn]
) -> None:
    db.execute(delete(PostingRuleLine).where(PostingRuleLine.rule_id == rule_id))
    db.flush()
    for i, ln in enumerate(lines, 1):
        db.add(
            PostingRuleLine(
                id=new_id(),
                organization_id=org_id,
                rule_id=rule_id,
                position=i,
                account_id=ln.account_id,
                side=ln.side,
                amount_source=ln.amount_source,
                memo=ln.memo,
            )
        )
    db.flush()


# ---------------- CRUD и жизненный цикл ----------------


def list_rules(
    db: Session, principal: Principal, org_id: uuid.UUID, lq: ListQuery, *, status: str
) -> Page[RuleListItem]:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    stmt = select(PostingRule).where(PostingRule.organization_id == org_id)
    if status in ("DRAFT", "APPROVED", "RETIRED"):
        stmt = stmt.where(PostingRule.status == status)
    stmt = search(stmt, lq.q, PostingRule.rule_key, PostingRule.name)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map={"rule_key": PostingRule.rule_key, "created_at": PostingRule.created_at},
        default_sort="created_at",
        id_column=PostingRule.id,
        default_order="desc",
    )
    return Page[RuleListItem](
        items=[
            RuleListItem(
                id=r.id,
                rule_key=r.rule_key,
                version=r.version,
                name=r.name,
                doc_type=r.doc_type,
                status=r.status,
                effective_from=r.effective_from,
                effective_to=r.effective_to,
                approved_at=r.approved_at,
            )
            for r in rows
        ],
        total=total,
        page=lq.page,
        page_size=lq.page_size,
    )


def get_rule(db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID) -> RuleOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    return _out(db, _get(db, org_id, rid))


def create_rule(db: Session, principal: Principal, org_id: uuid.UUID, data: RuleCreate) -> RuleOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    _check_header(data.doc_type, data.effective_from, data.effective_to)
    exists = db.scalar(
        select(func.count())
        .select_from(PostingRule)
        .where(PostingRule.organization_id == org_id, PostingRule.rule_key == data.rule_key)
    )
    if exists:
        raise _fail(
            409,
            "rule_key_taken",
            "Правило с таким ключом уже есть: создайте новую версию.",
            "rule_key",
        )
    _check_lines(db, org_id, data.lines)
    r = PostingRule(
        id=new_id(),
        organization_id=org_id,
        rule_key=data.rule_key,
        version=1,
        name=data.name,
        description=data.description,
        doc_type=data.doc_type,
        status="DRAFT",
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        created_by=principal.user.id,
    )
    db.add(r)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409,
            "rule_key_taken",
            "Правило с таким ключом уже есть: создайте новую версию.",
            "rule_key",
        ) from exc
    _write_lines(db, org_id, r.id, data.lines)
    audit.record(
        db,
        action="posting_rule.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="posting_rule",
        object_id=r.id,
        after={"rule_key": r.rule_key, "version": 1, "doc_type": r.doc_type},
    )
    db.commit()
    return _out(db, r)


def update_rule(
    db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID, data: RuleUpdate
) -> RuleOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    r = _get(db, org_id, rid, lock=True)
    if r.status != "DRAFT":
        raise AppError(
            409, "rule_not_draft", "Утверждённое правило изменить нельзя: создайте новую версию."
        )
    sent = data.model_fields_set
    doc_type = data.doc_type if "doc_type" in sent and data.doc_type else r.doc_type
    eff_from = data.effective_from if "effective_from" in sent else r.effective_from
    eff_to = data.effective_to if "effective_to" in sent else r.effective_to
    _check_header(doc_type, eff_from, eff_to)
    if data.lines is not None:
        _check_lines(db, org_id, data.lines)
    if "name" in sent and data.name:
        r.name = data.name
    if "description" in sent:
        r.description = data.description
    r.doc_type, r.effective_from, r.effective_to = doc_type, eff_from, eff_to
    if data.lines is not None:
        _write_lines(db, org_id, r.id, data.lines)
    audit.record(
        db,
        action="posting_rule.updated",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="posting_rule",
        object_id=r.id,
        after={"rule_key": r.rule_key, "version": r.version},
    )
    db.commit()
    return _out(db, r)


def delete_rule(db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID) -> None:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    r = _get(db, org_id, rid, lock=True)
    if r.status != "DRAFT":
        raise AppError(409, "rule_not_draft", "Удалить можно только черновик правила.")
    db.execute(delete(PostingRuleLine).where(PostingRuleLine.rule_id == r.id))
    db.delete(r)
    audit.record(
        db,
        action="posting_rule.deleted",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="posting_rule",
        object_id=rid,
        before={"rule_key": r.rule_key, "version": r.version},
    )
    db.commit()


def approve_rule(
    db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID, data: RuleApprove
) -> RuleOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    r = _get(db, org_id, rid, lock=True)
    if r.status != "DRAFT":
        raise AppError(409, "rule_not_draft", "Правило уже утверждено или выведено из действия.")
    lines = [
        RuleLineIn(
            account_id=ln.account_id, side=ln.side, amount_source=ln.amount_source, memo=ln.memo
        )
        for ln in db.scalars(
            select(PostingRuleLine)
            .where(PostingRuleLine.rule_id == r.id)
            .order_by(PostingRuleLine.position)
        )
    ]
    _check_lines(db, org_id, lines)  # счета могли быть архивированы с момента ввода
    now = utcnow()
    # Предыдущая действующая версия этого правила выводится из действия.
    for old in db.scalars(
        select(PostingRule)
        .where(
            PostingRule.organization_id == org_id,
            PostingRule.rule_key == r.rule_key,
            PostingRule.status == "APPROVED",
            PostingRule.id != r.id,
        )
        .with_for_update()
    ):
        old.status, old.retired_at = "RETIRED", now
    r.status, r.legal_basis, r.approved_by, r.approved_at = (
        "APPROVED",
        data.legal_basis,
        principal.user.id,
        now,
    )
    audit.record(
        db,
        action="posting_rule.approved",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="posting_rule",
        object_id=r.id,
        after={"rule_key": r.rule_key, "version": r.version, "legal_basis": data.legal_basis},
    )
    db.commit()
    return _out(db, r)


def retire_rule(db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID) -> RuleOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    r = _get(db, org_id, rid, lock=True)
    if r.status != "APPROVED":
        raise AppError(
            409, "rule_not_approved", "Вывести из действия можно только утверждённое правило."
        )
    r.status, r.retired_at = "RETIRED", utcnow()
    audit.record(
        db,
        action="posting_rule.retired",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="posting_rule",
        object_id=r.id,
        after={"rule_key": r.rule_key, "version": r.version},
    )
    db.commit()
    return _out(db, r)


def new_version(db: Session, principal: Principal, org_id: uuid.UUID, rid: uuid.UUID) -> RuleOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    src = _get(db, org_id, rid)
    last = db.scalar(
        select(func.max(PostingRule.version)).where(
            PostingRule.organization_id == org_id, PostingRule.rule_key == src.rule_key
        )
    )
    draft_exists = db.scalar(
        select(func.count())
        .select_from(PostingRule)
        .where(
            PostingRule.organization_id == org_id,
            PostingRule.rule_key == src.rule_key,
            PostingRule.status == "DRAFT",
        )
    )
    if draft_exists:
        raise AppError(409, "rule_draft_exists", "У этого правила уже есть черновик новой версии.")
    r = PostingRule(
        id=new_id(),
        organization_id=org_id,
        rule_key=src.rule_key,
        version=int(last or 0) + 1,
        name=src.name,
        description=src.description,
        doc_type=src.doc_type,
        status="DRAFT",
        effective_from=src.effective_from,
        effective_to=src.effective_to,
        created_by=principal.user.id,
    )
    db.add(r)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409, "rule_draft_exists", "Версия уже создана параллельным запросом."
        ) from exc
    for ln in db.scalars(
        select(PostingRuleLine)
        .where(PostingRuleLine.rule_id == src.id)
        .order_by(PostingRuleLine.position)
    ):
        db.add(
            PostingRuleLine(
                id=new_id(),
                organization_id=org_id,
                rule_id=r.id,
                position=ln.position,
                account_id=ln.account_id,
                side=ln.side,
                amount_source=ln.amount_source,
                memo=ln.memo,
            )
        )
    db.flush()
    audit.record(
        db,
        action="posting_rule.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="posting_rule",
        object_id=r.id,
        after={"rule_key": r.rule_key, "version": r.version, "from": str(src.id)},
    )
    db.commit()
    return _out(db, r)


# ---------------- предложения по документу ----------------


def _forbidden() -> AppError:
    return AppError(403, "forbidden", "Недостаточно прав для этого действия.")


def _load_document(
    db: Session, org_id: uuid.UUID, doc_id: uuid.UUID, *, lock: bool = False
) -> Document:
    stmt = select(Document).where(Document.id == doc_id, Document.organization_id == org_id)
    doc = db.scalar(stmt.with_for_update() if lock else stmt)
    if doc is None:
        raise AppError(404, "not_found", "Документ не найден.")
    return doc


def _posted_entry(
    db: Session, org_id: uuid.UUID, doc_id: uuid.UUID, rule_key: str
) -> uuid.UUID | None:
    """Действующая (не сторнированная) проводка по документу и этому правилу (любой версии)."""
    rev = select(JournalEntry.reverses_id).where(
        JournalEntry.organization_id == org_id, JournalEntry.reverses_id.is_not(None)
    )
    return db.scalar(
        select(JournalEntry.id)
        .join(
            PostingRule,
            (PostingRule.id == JournalEntry.rule_id)
            & (PostingRule.organization_id == JournalEntry.organization_id),
        )
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.source_type == "document",
            JournalEntry.source_id == doc_id,
            PostingRule.rule_key == rule_key,
            JournalEntry.reverses_id.is_(None),
            JournalEntry.id.not_in(rev),
        )
        .limit(1)
    )


def _proposal(
    db: Session, org_id: uuid.UUID, doc: Document, rule: PostingRule, entry_date: date | None
) -> ProposalOut:
    problems: list[str] = []
    d = entry_date or doc.doc_date
    if d is None:
        problems.append("document_date_missing")
    if doc.currency is None:
        problems.append("document_currency_missing")
    if doc.total_amount is None:
        problems.append("document_total_missing")
    rows = db.execute(
        select(PostingRuleLine, Account)
        .join(
            Account,
            (Account.id == PostingRuleLine.account_id)
            & (Account.organization_id == PostingRuleLine.organization_id),
        )
        .where(PostingRuleLine.rule_id == rule.id)
        .order_by(PostingRuleLine.position)
    ).all()
    minor = _minor(db, doc.currency) if doc.currency else 2
    lines: list[ProposalLine] = []
    total = doc.total_amount
    tax = doc.tax_total
    needs_tax = any(ln.amount_source in ("tax_total", "total_minus_tax") for ln, _ in rows)
    if needs_tax and tax is None and "document_total_missing" not in problems:
        problems.append("document_tax_missing")
    if not problems:
        assert total is not None
        t = tax if tax is not None else ZERO
        values = {"total_amount": total, "tax_total": t, "total_minus_tax": total - t}
        for ln, a in rows:
            amount = values[ln.amount_source]
            if amount < 0:
                problems.append("amount_negative")
                break
            if amount == 0:
                continue  # нулевая строка не проводится
            if -amount.normalize().as_tuple().exponent > minor:  # type: ignore[operator]
                problems.append("too_many_decimals")
                break
            lines.append(
                ProposalLine(
                    account_id=a.id,
                    account_code=a.code,
                    account_name=a.name,
                    debit=money(amount, minor) if ln.side == "debit" else money(ZERO, minor),
                    credit=money(amount, minor) if ln.side == "credit" else money(ZERO, minor),
                    memo=ln.memo,
                )
            )
        if not problems and len(lines) < 2:
            problems.append("no_postable_lines")
    enabled = (
        db.scalar(
            select(OrganizationCurrency.enabled).where(
                OrganizationCurrency.organization_id == org_id,
                OrganizationCurrency.currency_code == doc.currency,
            )
        )
        if doc.currency
        else None
    )
    if doc.currency and not enabled:
        problems.append("currency_not_enabled")
    description = f"{rule.name}: {doc.doc_type}" + (f" № {doc.number}" if doc.number else "")
    warnings: list[str] = []
    if tax_mismatch(db, org_id, doc.total_amount, doc.tax_total, d, doc.currency):
        warnings.append("tax_rate_mismatch")
    return ProposalOut(
        rule=RuleRef(id=rule.id, key=rule.rule_key, version=rule.version, name=rule.name),
        warnings=warnings,
        legal_basis=rule.legal_basis,
        entry_date=d,
        currency=doc.currency,
        description=description,
        lines=lines,
        problems=problems,
        posted_entry_id=_posted_entry(db, org_id, doc.id, rule.rule_key),
    )


def document_proposals(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID
) -> list[ProposalOut]:
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    if rbac.DOCUMENTS_READ not in acc.permissions:
        raise _forbidden()
    doc = _load_document(db, org_id, doc_id)
    if doc.status != "APPROVED":
        raise _fail(
            409, "document_not_approved", "Проводки предлагаются только по утверждённому документу."
        )
    rules = db.scalars(
        select(PostingRule)
        .where(
            PostingRule.organization_id == org_id,
            PostingRule.status == "APPROVED",
            PostingRule.doc_type == doc.doc_type,
        )
        .order_by(PostingRule.rule_key)
    ).all()
    out: list[ProposalOut] = []
    for r in rules:
        d = doc.doc_date
        if d and (
            (r.effective_from and d < r.effective_from) or (r.effective_to and d > r.effective_to)
        ):
            continue  # правило не действует на дату документа
        out.append(_proposal(db, org_id, doc, r, None))
    return out


def post_from_document(
    db: Session, principal: Principal, org_id: uuid.UUID, data: FromDocumentIn
) -> EntryOut:
    """Проведение предложения: сумма пересчитывается на сервере по утверждённому правилу, а не берётся от клиента."""
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_POST)
    if rbac.DOCUMENTS_READ not in acc.permissions:
        raise _forbidden()
    doc = _load_document(
        db, org_id, data.document_id, lock=True
    )  # сериализует повторные/параллельные проведения
    if doc.status != "APPROVED":
        raise _fail(
            409, "document_not_approved", "Проводки возможны только по утверждённому документу."
        )
    rule = _get(db, org_id, data.rule_id)
    if rule.status != "APPROVED":
        raise _fail(409, "rule_not_approved", "Правило не утверждено или выведено из действия.")
    if rule.doc_type != doc.doc_type:
        raise _fail(422, "rule_doc_type_mismatch", "Правило относится к другому типу документа.")
    p = _proposal(db, org_id, doc, rule, data.entry_date)
    if p.entry_date and (
        (rule.effective_from and p.entry_date < rule.effective_from)
        or (rule.effective_to and p.entry_date > rule.effective_to)
    ):
        raise _fail(422, "rule_not_effective", "Правило не действует на эту дату.")
    if p.posted_entry_id is not None:
        raise _fail(
            409,
            "document_already_posted",
            "По этому документу уже проведена проводка по этому правилу.",
        )
    if p.problems:
        raise AppError(
            422,
            "proposal_not_ready",
            "Проводку по документу сделать нельзя.",
            [{"field": "", "code": c, "message": c, "params": {}} for c in p.problems],
        )
    assert p.entry_date is not None and p.currency is not None
    return _insert_entry(
        db,
        principal,
        org_id,
        p.entry_date,
        p.currency,
        p.description,
        [(ln.account_id, Decimal(ln.debit), Decimal(ln.credit), ln.memo) for ln in p.lines],
        reverses=None,
        source=("document", doc.id, rule.id, rule.version),
    )
