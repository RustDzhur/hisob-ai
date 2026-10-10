"""Применение ответа AI к документу: заполняются только ПУСТЫЕ поля; всё остальное — подсказки.

AI не утверждает, не проводит и не создаёт учётных записей: он лишь предлагает значения для проверки человеком.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.documents.extraction import matching
from app.modules.documents.extraction.parsing import digits_only, parse_date, parse_decimal
from app.modules.documents.extraction.schema import ExtractionOut, FieldOut
from app.modules.documents.models import DOC_TYPES, AIJob, Document, DocumentLine, ExtractedField
from app.modules.tenancy.models import Currency, Organization, OrganizationCurrency
from app.platform.base import new_id
from app.platform.validators import tin_length_usual

QTY_DECIMALS = 6
PRICE_DECIMALS = 4


@dataclass
class ApplySummary:
    applied: int = 0
    suggested: int = 0
    lines_created: int = 0


def _conf(f: FieldOut) -> Decimal | None:
    return Decimal(str(round(f.confidence, 3))) if f.confidence is not None else None


def apply_extraction(
    db: Session,
    org_id: uuid.UUID,
    doc: Document,
    job: AIJob,
    out: ExtractionOut,
    existing_lines: int,
) -> tuple[ApplySummary, dict[str, Any]]:
    summary = ApplySummary()
    rows: list[ExtractedField] = []

    def record(
        path: str, f: FieldOut, validation: str, applied: bool, value: str | None = None
    ) -> None:
        shown = value if value is not None else f.value
        if shown is None:
            return
        rows.append(
            ExtractedField(
                id=new_id(),
                organization_id=org_id,
                document_id=doc.id,
                job_id=job.id,
                path=path,
                value=shown,
                confidence=_conf(f),
                evidence=f.evidence,
                validation=validation,
                applied=applied,
            )
        )
        summary.applied += int(applied)
        summary.suggested += int(not applied)

    # --- тип документа ---
    t = out.doc_type.value
    if t is not None:
        ok = t in DOC_TYPES
        use = ok and doc.doc_type == "other" and t != "other"
        if use:
            doc.doc_type = t
        record("doc_type", out.doc_type, "ok" if ok else "invalid", use)

    # --- номер, дата ---
    if out.number.value is not None:
        use = doc.number is None
        if use:
            doc.number = out.number.value[:100]
        record("number", out.number, "ok", use)
    if out.date.value is not None:
        d = parse_date(out.date.value)
        use = d is not None and doc.doc_date is None
        if use:
            doc.doc_date = d
        record("date", out.date, "ok" if d else "invalid", use)

    # --- валюта (только включённая в организации) ---
    cur = (out.currency.value or "").strip().upper()
    cur_ok = False
    if out.currency.value is not None:
        cur_ok = (
            len(cur) == 3
            and db.get(Currency, cur) is not None
            and bool(
                db.scalar(
                    select(OrganizationCurrency.enabled).where(
                        OrganizationCurrency.organization_id == org_id,
                        OrganizationCurrency.currency_code == cur,
                    )
                )
            )
        )
        use = cur_ok and doc.currency is None
        if use:
            doc.currency = cur
        record(
            "currency",
            out.currency,
            "ok" if cur_ok else "invalid",
            use,
            value=cur or out.currency.value,
        )
    minor = (
        db.scalar(select(Currency.minor_units).where(Currency.code == doc.currency))
        if doc.currency
        else None
    )

    # --- суммы документа ---
    for path, f, attr in (
        ("total_amount", out.total_amount, "total_amount"),
        ("tax_total", out.tax_total, "tax_total"),
    ):
        if f.value is None:
            continue
        v = parse_decimal(f.value, max_decimals=minor)
        valid = v is not None and v >= 0
        use = valid and getattr(doc, attr) is None
        if use:
            setattr(doc, attr, v)
        record(path, f, "ok" if valid else "invalid", use)

    # --- стороны и сопоставление контрагента ---
    # ИНН необычной длины (не 9/14 знаков) — вероятная ошибка чтения: не используем для сопоставления
    # и показываем как не прошедший проверку.
    s_tin, b_tin = digits_only(out.seller_tin.value), digits_only(out.buyer_tin.value)
    seller = matching.Party(out.seller_name.value, s_tin if tin_length_usual(s_tin) else None)
    buyer = matching.Party(out.buyer_name.value, b_tin if tin_length_usual(b_tin) else None)
    for path, f in (
        ("seller_name", out.seller_name),
        ("seller_tin", out.seller_tin),
        ("buyer_name", out.buyer_name),
        ("buyer_tin", out.buyer_tin),
    ):
        is_tin = path.endswith("_tin")
        bad = is_tin and f.value is not None and not tin_length_usual(digits_only(f.value))
        record(path, f, "invalid" if bad else "ok", False)
    org = db.get(Organization, org_id)
    apply_cp, cp_candidates = matching.match_counterparty(
        db, org_id, org.tin if org else None, seller, buyer
    )
    if apply_cp is not None and doc.counterparty_id is None:
        doc.counterparty_id = apply_cp
        summary.applied += 1

    # --- строки ---
    units = matching.unit_resolver(db, org_id)
    fill_lines = existing_lines == 0
    line_matches: dict[str, list[dict[str, Any]]] = {}
    for n, ln in enumerate(out.lines, 1):
        qty = parse_decimal(ln.quantity.value, max_decimals=QTY_DECIMALS)
        qty = qty if qty is not None and qty >= 0 else None
        price = parse_decimal(ln.unit_price.value, max_decimals=PRICE_DECIMALS)
        price = price if price is not None and price >= 0 else None
        money: dict[str, Decimal | None] = {}
        for key, f in (
            ("discount", ln.discount),
            ("tax_amount", ln.tax_amount),
            ("amount", ln.amount),
        ):
            v = parse_decimal(f.value, max_decimals=minor)
            money[key] = v if v is not None and v >= 0 else None
        unit_id = units.resolve(ln.unit.value)
        prod_id, prod_cands = matching.match_product(
            db, org_id, ln.article.value, ln.description.value
        )
        if prod_cands:
            line_matches[str(n)] = prod_cands

        parsed: dict[str, Any] = {
            "quantity": qty,
            "unit_price": price,
            "discount": money["discount"],
            "tax_amount": money["tax_amount"],
            "amount": money["amount"],
        }
        for key, f in (
            ("description", ln.description),
            ("quantity", ln.quantity),
            ("unit", ln.unit),
            ("unit_price", ln.unit_price),
            ("discount", ln.discount),
            ("tax_amount", ln.tax_amount),
            ("amount", ln.amount),
            ("article", ln.article),
        ):
            if f.value is None:
                continue
            if key in parsed:
                valid = parsed[key] is not None
            elif key == "unit":
                valid = unit_id is not None
            else:
                valid = True
            record(f"lines.{n}.{key}", f, "ok" if valid else "invalid", fill_lines and valid)
        if fill_lines:
            db.add(
                DocumentLine(
                    id=new_id(),
                    organization_id=org_id,
                    document_id=doc.id,
                    position=n,
                    description=(ln.description.value or "—")[:500],
                    product_id=prod_id,
                    unit_id=unit_id,
                    quantity=qty,
                    unit_price=price,
                    discount=money["discount"],
                    tax_amount=money["tax_amount"],
                    amount=money["amount"],
                )
            )
            summary.lines_created += 1
            summary.applied += int(prod_id is not None) + int(
                unit_id is not None and ln.unit.value is None
            )

    db.add_all(rows)
    return summary, {"counterparty": cp_candidates, "lines": line_matches}
