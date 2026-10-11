"""Курсы валют и налоговые коды (срез 5.3).

Курсы: вводятся вручную или загружаются с cbu.uz (только при базовой валюте UZS), всегда с датой и источником.
Переоценки и курсовых разниц нет: курсы используются для ОТЧЁТА в базовой валюте и справки.
Налоговые коды: только НДС; ставку и основание вводит бухгалтер (можно добавить справочную запись с источниками,
она остаётся черновиком до подтверждения). Используется как предупреждение в предложении проводки, не как расчёт.
"""

import csv
import io
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.accounting import cbu
from app.modules.accounting.models import (
    Account,
    ExchangeRate,
    JournalEntry,
    JournalLine,
    TaxCode,
)
from app.modules.accounting.schemas import (
    BaseTrialBalanceOut,
    CbuImportIn,
    CbuImportOut,
    MissingRate,
    RateIn,
    RateOut,
    TaxCodeIn,
    TaxCodeOut,
    TaxCodeUpdate,
    TrialRow,
)
from app.modules.accounting.service import ZERO, _fail, _minor, money, trial_balance
from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import OrganizationCurrency
from app.platform.base import new_id, utcnow
from app.platform.errors import AppError
from app.platform.listing import ListQuery, Page, paginate

REFERENCE_NOTE = (
    "Справочная запись: стандартная ставка НДС в Узбекистане — 12 % (в 2026 году сохраняется; для производителей "
    "сельхозпродукции при реализации — 0 %). Источники: бюджетное послание на 2026 год (spot.uz, 26.11.2025), "
    "Deloitte Tax Alert 2023 (снижение с 15 % до 12 % с 01.01.2023). Номер статьи Налогового кодекса не проверялся. "
    "Бухгалтер обязан проверить по действующей редакции Налогового кодекса (lex.uz) и подтвердить."
)
REFERENCE_URL = "https://www.spot.uz/ru/2025/11/26/taxes-for-2026/"


# ---------------- курсы ----------------


def _rate_out(r: ExchangeRate) -> RateOut:
    return RateOut(
        id=r.id,
        currency=r.currency_code,
        rate_date=r.rate_date,
        nominal=r.nominal,
        rate=format(Decimal(r.rate).quantize(Decimal("0.000001")), "f"),
        source=r.source,
        source_ref=r.source_ref,
        created_at=r.created_at,
    )


def list_rates(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    lq: ListQuery,
    *,
    currency: str | None,
    date_from: date | None,
    date_to: date | None,
) -> Page[RateOut]:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    stmt = select(ExchangeRate).where(ExchangeRate.organization_id == org_id)
    if currency:
        stmt = stmt.where(ExchangeRate.currency_code == currency.upper())
    if date_from:
        stmt = stmt.where(ExchangeRate.rate_date >= date_from)
    if date_to:
        stmt = stmt.where(ExchangeRate.rate_date <= date_to)
    rows, total = paginate(
        db,
        stmt,
        lq,
        sort_map={"rate_date": ExchangeRate.rate_date, "currency": ExchangeRate.currency_code},
        default_sort="rate_date",
        id_column=ExchangeRate.id,
        default_order="desc",
    )
    return Page[RateOut](
        items=[_rate_out(r) for r in rows], total=total, page=lq.page, page_size=lq.page_size
    )


def _check_currency(db: Session, org_id: uuid.UUID, base: str, currency: str) -> None:
    if currency == base:
        raise _fail(
            422, "rate_for_base_currency", "Для базовой валюты курс не задаётся.", "currency"
        )
    enabled = db.scalar(
        select(OrganizationCurrency.enabled).where(
            OrganizationCurrency.organization_id == org_id,
            OrganizationCurrency.currency_code == currency,
        )
    )
    if not enabled:
        raise _fail(422, "currency_not_enabled", "Валюта не включена в организации.", "currency")


def put_rate(db: Session, principal: Principal, org_id: uuid.UUID, data: RateIn) -> RateOut:
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    _check_currency(db, org_id, acc.org.base_currency, data.currency)
    r = db.scalar(
        select(ExchangeRate)
        .where(
            ExchangeRate.organization_id == org_id,
            ExchangeRate.currency_code == data.currency,
            ExchangeRate.rate_date == data.rate_date,
        )
        .with_for_update()
    )
    before = format(r.rate, "f") if r else None
    if r is None:
        r = ExchangeRate(
            id=new_id(),
            organization_id=org_id,
            currency_code=data.currency,
            rate_date=data.rate_date,
            nominal=data.nominal,
            rate=data.rate,
            source="manual",
            created_by=principal.user.id,
        )
        db.add(r)
    else:
        r.nominal, r.rate, r.source, r.source_ref, r.created_by = (
            data.nominal,
            data.rate,
            "manual",
            None,
            principal.user.id,
        )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(409, "rate_conflict", "Курс уже задан параллельным запросом.") from exc
    audit.record(
        db,
        action="exchange_rate.set",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="exchange_rate",
        object_id=r.id,
        before={"rate": before} if before else None,
        after={
            "currency": data.currency,
            "rate_date": data.rate_date.isoformat(),
            "rate": format(data.rate, "f"),
            "nominal": data.nominal,
        },
    )
    db.commit()
    return _rate_out(r)


def import_cbu(
    db: Session, principal: Principal, org_id: uuid.UUID, data: CbuImportIn
) -> CbuImportOut:
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    if acc.org.base_currency != "UZS":
        raise _fail(
            422,
            "rates_base_not_uzs",
            "Курсы ЦБ Узбекистана задаются к сумам: базовая валюта организации не UZS.",
        )
    if data.rate_date > utcnow().date():
        raise _fail(422, "rate_date_in_future", "Курсы на будущую дату недоступны.", "rate_date")
    try:
        fetched = cbu.fetch_rates(data.rate_date)
    except cbu.CbuError as exc:
        status = 404 if exc.code == "rates_not_published" else 502
        raise AppError(status, exc.code, exc.message) from exc
    enabled = {
        r[0]
        for r in db.execute(
            select(OrganizationCurrency.currency_code).where(
                OrganizationCurrency.organization_id == org_id,
                OrganizationCurrency.enabled.is_(True),
            )
        )
    }
    existing = {
        r[0]
        for r in db.execute(
            select(ExchangeRate.currency_code).where(
                ExchangeRate.organization_id == org_id, ExchangeRate.rate_date == data.rate_date
            )
        )
    }
    imported: list[str] = []
    skipped: list[str] = []
    not_enabled: list[str] = []
    for r in sorted(fetched, key=lambda x: x.currency):
        if r.currency == "UZS":
            continue
        if r.currency not in enabled:
            not_enabled.append(r.currency)
        elif r.currency in existing:
            skipped.append(r.currency)  # ручные и ранее загруженные курсы не перезаписываются
        else:
            db.add(
                ExchangeRate(
                    id=new_id(),
                    organization_id=org_id,
                    currency_code=r.currency,
                    rate_date=data.rate_date,
                    nominal=r.nominal,
                    rate=r.rate,
                    source="cbu",
                    source_ref=cbu.source_ref(data.rate_date),
                    created_by=principal.user.id,
                )
            )
            imported.append(r.currency)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(409, "rate_conflict", "Курсы уже загружены параллельным запросом.") from exc
    audit.record(
        db,
        action="exchange_rate.imported",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="exchange_rate",
        after={"rate_date": data.rate_date.isoformat(), "source": "cbu", "imported": imported},
    )
    db.commit()
    return CbuImportOut(
        rate_date=data.rate_date,
        imported=imported,
        skipped_existing=skipped,
        not_enabled=not_enabled[:80],
    )


def _lookup(db: Session, org_id: uuid.UUID, currency: str, d: date) -> ExchangeRate | None:
    """Последний курс на дату или ранее (если на сам день не опубликован — действует предыдущий)."""
    return db.scalar(
        select(ExchangeRate)
        .where(
            ExchangeRate.organization_id == org_id,
            ExchangeRate.currency_code == currency,
            ExchangeRate.rate_date <= d,
        )
        .order_by(ExchangeRate.rate_date.desc())
        .limit(1)
    )


def lookup_rate(
    db: Session, principal: Principal, org_id: uuid.UUID, currency: str, d: date
) -> RateOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    r = _lookup(db, org_id, currency.upper(), d)
    if r is None:
        raise AppError(404, "rate_not_found", "Курс на эту дату не найден.")
    return _rate_out(r)


# ---------------- налоговые коды ----------------


def _tax_out(db: Session, t: TaxCode) -> TaxCodeOut:
    name = (
        db.scalar(select(User.full_name).where(User.id == t.confirmed_by))
        if t.confirmed_by
        else None
    )
    return TaxCodeOut(
        id=t.id,
        code=t.code,
        name=t.name,
        kind=t.kind,
        rate=format(t.rate.normalize(), "f"),
        effective_from=t.effective_from,
        effective_to=t.effective_to,
        source_url=t.source_url,
        source_note=t.source_note,
        status=t.status,
        is_default=t.is_default,
        confirmed_by_name=name,
        confirmed_at=t.confirmed_at,
    )


def _get_tax(db: Session, org_id: uuid.UUID, tid: uuid.UUID, *, lock: bool = False) -> TaxCode:
    stmt = select(TaxCode).where(TaxCode.id == tid, TaxCode.organization_id == org_id)
    t = db.scalar(stmt.with_for_update() if lock else stmt)
    if t is None:
        raise AppError(404, "not_found", "Налоговый код не найден.")
    return t


def list_tax_codes(db: Session, principal: Principal, org_id: uuid.UUID) -> list[TaxCodeOut]:
    authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    rows = db.scalars(
        select(TaxCode).where(TaxCode.organization_id == org_id).order_by(TaxCode.code)
    ).all()
    return [_tax_out(db, t) for t in rows]


def _check_tax_dates(f: date | None, t: date | None) -> None:
    if f and t and t < f:
        raise _fail(422, "invalid_range", "Конец действия раньше начала.", "effective_to")


def create_tax_code(
    db: Session, principal: Principal, org_id: uuid.UUID, data: TaxCodeIn
) -> TaxCodeOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    _check_tax_dates(data.effective_from, data.effective_to)
    t = TaxCode(
        id=new_id(),
        organization_id=org_id,
        code=data.code,
        name=data.name,
        kind="vat",
        rate=data.rate,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        source_url=data.source_url,
        source_note=data.source_note,
        status="DRAFT",
        created_by=principal.user.id,
    )
    return _save_new_tax(db, principal, org_id, t)


def _save_new_tax(db: Session, principal: Principal, org_id: uuid.UUID, t: TaxCode) -> TaxCodeOut:
    db.add(t)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(409, "tax_code_taken", "Налоговый код с таким кодом уже есть.", "code") from exc
    audit.record(
        db,
        action="tax_code.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="tax_code",
        object_id=t.id,
        after={"code": t.code, "rate": format(t.rate.normalize(), "f")},
    )
    db.commit()
    return _tax_out(db, t)


def suggest_reference(db: Session, principal: Principal, org_id: uuid.UUID) -> TaxCodeOut:
    """Справочная запись «НДС 12 %» с источниками — как ЧЕРНОВИК, который должен подтвердить бухгалтер."""
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    t = TaxCode(
        id=new_id(),
        organization_id=org_id,
        code="VAT-12",
        name="НДС 12 % (справочная запись)",
        kind="vat",
        rate=Decimal(12),
        source_url=REFERENCE_URL,
        source_note=REFERENCE_NOTE,
        status="DRAFT",
        created_by=principal.user.id,
    )
    return _save_new_tax(db, principal, org_id, t)


def update_tax_code(
    db: Session, principal: Principal, org_id: uuid.UUID, tid: uuid.UUID, data: TaxCodeUpdate
) -> TaxCodeOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    t = _get_tax(db, org_id, tid, lock=True)
    if t.status != "DRAFT":
        raise AppError(
            409,
            "tax_code_confirmed",
            "Подтверждённый налоговый код изменить нельзя: создайте новый.",
        )
    sent = data.model_fields_set
    f = data.effective_from if "effective_from" in sent else t.effective_from
    to = data.effective_to if "effective_to" in sent else t.effective_to
    _check_tax_dates(f, to)
    if "name" in sent and data.name:
        t.name = data.name
    if "rate" in sent and data.rate is not None:
        t.rate = data.rate
    t.effective_from, t.effective_to = f, to
    if "source_url" in sent:
        t.source_url = data.source_url
    if "source_note" in sent:
        t.source_note = data.source_note
    audit.record(
        db,
        action="tax_code.updated",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="tax_code",
        object_id=t.id,
        after={"code": t.code, "rate": format(t.rate.normalize(), "f")},
    )
    db.commit()
    return _tax_out(db, t)


def confirm_tax_code(
    db: Session, principal: Principal, org_id: uuid.UUID, tid: uuid.UUID
) -> TaxCodeOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    t = _get_tax(db, org_id, tid, lock=True)
    if t.status == "CONFIRMED":
        return _tax_out(db, t)
    if not (t.source_note or t.source_url):
        raise _fail(
            422, "tax_source_required", "Укажите основание (источник) ставки.", "source_note"
        )
    t.status, t.confirmed_by, t.confirmed_at = "CONFIRMED", principal.user.id, utcnow()
    audit.record(
        db,
        action="tax_code.confirmed",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="tax_code",
        object_id=t.id,
        after={"code": t.code, "rate": format(t.rate.normalize(), "f")},
    )
    db.commit()
    return _tax_out(db, t)


def set_default_tax_code(
    db: Session, principal: Principal, org_id: uuid.UUID, tid: uuid.UUID
) -> TaxCodeOut:
    authorize(db, principal, org_id, rbac.ACCOUNTING_MANAGE)
    t = _get_tax(db, org_id, tid, lock=True)
    if t.status != "CONFIRMED":
        raise AppError(
            409,
            "tax_code_not_confirmed",
            "Основным можно сделать только подтверждённый налоговый код.",
        )
    for other in db.scalars(
        select(TaxCode)
        .where(
            TaxCode.organization_id == org_id,
            TaxCode.kind == t.kind,
            TaxCode.is_default.is_(True),
            TaxCode.id != t.id,
        )
        .with_for_update()
    ):
        other.is_default = False
    db.flush()
    t.is_default = True
    audit.record(
        db,
        action="tax_code.default_set",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="tax_code",
        object_id=t.id,
        after={"code": t.code},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _fail(
            409, "tax_code_conflict", "Основной код уже меняется параллельным запросом."
        ) from exc
    return _tax_out(db, t)


def default_vat(db: Session, org_id: uuid.UUID, on: date | None) -> TaxCode | None:
    t = db.scalar(
        select(TaxCode).where(
            TaxCode.organization_id == org_id,
            TaxCode.kind == "vat",
            TaxCode.is_default.is_(True),
            TaxCode.status == "CONFIRMED",
        )
    )
    if t is None:
        return None
    if on and (
        (t.effective_from and on < t.effective_from) or (t.effective_to and on > t.effective_to)
    ):
        return None
    return t


def tax_mismatch(
    db: Session,
    org_id: uuid.UUID,
    total: Decimal | None,
    tax: Decimal | None,
    on: date | None,
    currency: str | None,
) -> bool:
    """Предупреждение: налог документа заметно отличается от «итого без налога × ставка» по основному коду НДС.

    Допуск предварительный (R-налоги): больший из двух минимальных единиц валюты и 0,5 % ожидаемого налога.
    """
    if total is None or tax is None or currency is None:
        return False
    code = default_vat(db, org_id, on)
    if code is None:
        return False
    net = total - tax
    if net <= 0:
        return False
    expected = net * code.rate / Decimal(100)
    minor = _minor(db, currency)
    tolerance = max(Decimal(2) * Decimal(1).scaleb(-minor), expected * Decimal("0.005"))
    return abs(tax - expected) > tolerance


# ---------------- отчёт в базовой валюте и экспорт ----------------


def _base_rows(
    db: Session, org_id: uuid.UUID, base: str, date_from: date, date_to: date
) -> tuple[list[TrialRow], bool, list[MissingRate], bool]:
    minor = _minor(db, base)
    q = Decimal(1).scaleb(-minor)
    in_range = (JournalEntry.entry_date >= date_from) & (JournalEntry.entry_date <= date_to)
    before = JournalEntry.entry_date < date_from
    rows = db.execute(
        select(
            Account.id,
            Account.code,
            Account.name,
            JournalEntry.currency,
            JournalEntry.entry_date,
            func.sum(case((before, JournalLine.debit - JournalLine.credit), else_=0)),
            func.sum(case((in_range, JournalLine.debit), else_=0)),
            func.sum(case((in_range, JournalLine.credit), else_=0)),
        )
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
        .where(Account.organization_id == org_id, JournalEntry.entry_date <= date_to)
        .group_by(
            Account.id, Account.code, Account.name, JournalEntry.currency, JournalEntry.entry_date
        )
    ).all()
    acc: dict[uuid.UUID, dict[str, Any]] = {}
    missing: dict[tuple[str, date], None] = {}
    cache: dict[tuple[str, date], tuple[Decimal, int] | None] = {}
    for aid, code, name, cur, d, op, td, tc in rows:
        if cur == base:
            factor: tuple[Decimal, int] | None = (Decimal(1), 1)
        else:
            key = (cur, d)
            if key not in cache:
                r = _lookup(db, org_id, cur, d)
                cache[key] = (r.rate, r.nominal) if r else None
            factor = cache[key]
        if factor is None:
            missing[(cur, d)] = None
            continue
        rate, nominal = factor

        def conv(v: Any, rate: Decimal = rate, nominal: int = nominal) -> Decimal:
            return (Decimal(v) * rate / nominal).quantize(q, rounding=ROUND_HALF_UP)

        slot = acc.setdefault(aid, {"code": code, "name": name, "op": ZERO, "td": ZERO, "tc": ZERO})
        slot["op"] += conv(op)
        slot["td"] += conv(td)
        slot["tc"] += conv(tc)

    def side(n: Decimal) -> tuple[Decimal, Decimal]:
        return (n, ZERO) if n >= 0 else (ZERO, -n)

    out: list[TrialRow] = []
    sums = [ZERO] * 6
    for aid, v in sorted(acc.items(), key=lambda kv: kv[1]["code"]):
        if v["op"] == 0 and v["td"] == 0 and v["tc"] == 0:
            continue
        od, oc = side(v["op"])
        cd, cc = side(v["op"] + v["td"] - v["tc"])
        out.append(
            TrialRow(
                account_id=aid,
                code=v["code"],
                name=v["name"],
                opening_debit=money(od, minor),
                opening_credit=money(oc, minor),
                turnover_debit=money(v["td"], minor),
                turnover_credit=money(v["tc"], minor),
                closing_debit=money(cd, minor),
                closing_credit=money(cc, minor),
            )
        )
        for i, x in enumerate((od, oc, v["td"], v["tc"], cd, cc)):
            sums[i] += x
    return (
        out,
        sums[2] == sums[3],
        [MissingRate(currency=c, rate_date=d) for c, d in sorted(missing)],
        bool(out),
    )


def base_trial_balance(
    db: Session, principal: Principal, org_id: uuid.UUID, *, date_from: date, date_to: date
) -> BaseTrialBalanceOut:
    acc = authorize(db, principal, org_id, rbac.ACCOUNTING_READ)
    if date_to < date_from:
        raise _fail(422, "invalid_range", "Конец периода раньше начала.", "date_to")
    base = acc.org.base_currency
    minor = _minor(db, base)
    rows, balanced, missing, _ = _base_rows(db, org_id, base, date_from, date_to)
    totals = None
    if rows:
        s = [
            sum((Decimal(getattr(r, f)) for r in rows), ZERO)
            for f in (
                "opening_debit",
                "opening_credit",
                "turnover_debit",
                "turnover_credit",
                "closing_debit",
                "closing_credit",
            )
        ]
        totals = TrialRow(
            account_id=uuid.UUID(int=0),
            code="",
            name="",
            opening_debit=money(s[0], minor),
            opening_credit=money(s[1], minor),
            turnover_debit=money(s[2], minor),
            turnover_credit=money(s[3], minor),
            closing_debit=money(s[4], minor),
            closing_credit=money(s[5], minor),
        )
    return BaseTrialBalanceOut(
        currency=base,
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        totals=totals,
        balanced=balanced and not missing,
        complete=not missing,
        missing_rates=missing,
        note="Суммы в иностранной валюте пересчитаны по курсу на дату проводки (последний известный на эту дату или ранее); "
        "каждая сумма округлена до знаков базовой валюты (half-up). Это справочный пересчёт, не переоценка: курсовые разницы не считаются.",
    )


def trial_balance_csv(
    db: Session,
    principal: Principal,
    org_id: uuid.UUID,
    *,
    date_from: date,
    date_to: date,
    currency: str | None,
    base: bool,
) -> str:
    if base:
        r = base_trial_balance(db, principal, org_id, date_from=date_from, date_to=date_to)
        cur, rows, totals = r.currency, r.rows, r.totals
    else:
        t = trial_balance(
            db, principal, org_id, date_from=date_from, date_to=date_to, currency=currency
        )
        cur, rows, totals = t.currency, t.rows, t.totals
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            f"Оборотно-сальдовая ведомость {date_from.isoformat()} — {date_to.isoformat()}, валюта {cur}"
        ]
    )
    w.writerow(
        [
            "Код",
            "Название",
            "Входящее Дт",
            "Входящее Кт",
            "Обороты Дт",
            "Обороты Кт",
            "Исходящее Дт",
            "Исходящее Кт",
        ]
    )
    for x in rows:
        w.writerow(
            [
                _safe(x.code),
                _safe(x.name),
                x.opening_debit,
                x.opening_credit,
                x.turnover_debit,
                x.turnover_credit,
                x.closing_debit,
                x.closing_credit,
            ]
        )
    if totals:
        w.writerow(
            [
                "",
                "Итого",
                totals.opening_debit,
                totals.opening_credit,
                totals.turnover_debit,
                totals.turnover_credit,
                totals.closing_debit,
                totals.closing_credit,
            ]
        )
    audit.record(
        db,
        action="export.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="report",
        after={
            "report": "trial_balance",
            "format": "csv",
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "currency": cur,
            "base": base,
            "rows": len(rows),
        },
    )
    db.commit()
    return buf.getvalue()


def _safe(v: str) -> str:
    """Защита от формул в таблицах: значения, начинающиеся с = + - @, получают апостроф."""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
