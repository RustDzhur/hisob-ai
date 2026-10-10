"""Проверки документа (ТЗ §8): обязательные поля, согласованность сумм, подозрительные ситуации.

Здесь НЕТ налоговых правил и правил округления: налоги вводятся как напечатано в документе, а допуск
при сравнении сумм — лишь порог «похоже на ошибку» (Q6/Q9 ждут бухгалтера). Серьёзность:
error — блокирует утверждение; warning — утверждение возможно только после явного подтверждения.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

# типы, у которых должны быть строки / контрагент / номер / итоговая сумма
LINE_TYPES = {"invoice", "esf", "waybill", "ttn", "act", "receipt", "return"}
NO_COUNTERPARTY = {"receipt", "other", "statement"}
NO_NUMBER = {"receipt", "other"}
NO_TOTAL = {"contract", "other"}


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # "error" | "warning"
    field: str | None = None
    params: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class LineData:
    position: int
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount: Decimal | None = None
    tax_amount: Decimal | None = None
    amount: Decimal | None = None


@dataclass(frozen=True)
class HeaderData:
    doc_type: str
    number: str | None
    doc_date: date | None
    has_counterparty: bool
    currency: str | None
    total_amount: Decimal | None
    tax_total: Decimal | None


def _s(d: Decimal, minor: int) -> str:
    """Точная строка: минимум знаков валюты, лишние нули не показываем."""
    digits = max(minor, -d.normalize().as_tuple().exponent)  # type: ignore[operator]
    return format(d.quantize(Decimal(1).scaleb(-digits)), "f")


def tolerance(minor_units: int, terms: int) -> Decimal:
    """Порог сравнения сумм: не меньше одной минимальной единицы валюты, +0,5 единицы на каждое слагаемое."""
    unit = Decimal(1).scaleb(-minor_units)
    return unit * max(Decimal(1), Decimal(terms) / 2)


def validate(
    header: HeaderData, lines: list[LineData], *, minor_units: int | None, today: date
) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append
    t = header.doc_type

    if header.doc_date is None:
        add(Issue("date_required", "error", "doc_date"))
    elif header.doc_date > today + timedelta(days=1):
        add(Issue("date_in_future", "warning", "doc_date"))
    if not header.number and t not in NO_NUMBER:
        add(Issue("number_required", "error", "number"))
    if not header.has_counterparty and t not in NO_COUNTERPARTY:
        add(Issue("counterparty_required", "error", "counterparty_id"))
    if header.total_amount is None and t not in NO_TOTAL:
        add(Issue("total_required", "error", "total_amount"))
    has_money = (
        header.total_amount is not None
        or header.tax_total is not None
        or any(
            v is not None
            for ln in lines
            for v in (ln.unit_price, ln.discount, ln.tax_amount, ln.amount)
        )
    )
    if has_money and header.currency is None:
        add(Issue("currency_required", "error", "currency"))
    if t in LINE_TYPES and not lines:
        add(Issue("lines_required", "error", "lines"))

    if minor_units is None:  # без валюты сравнивать суммы нельзя
        return issues

    # строка: сумма ≈ количество × цена − скидка
    incomplete = False
    for ln in lines:
        if ln.quantity is not None and ln.unit_price is not None and ln.amount is not None:
            expected = ln.quantity * ln.unit_price - (ln.discount or Decimal(0))
            if abs(expected - ln.amount) > tolerance(minor_units, 1):
                add(
                    Issue(
                        "line_amount_mismatch",
                        "warning",
                        "lines",
                        {
                            "position": ln.position,
                            "expected": _s(expected, minor_units),
                            "actual": _s(ln.amount, minor_units),
                        },
                    )
                )
        elif ln.amount is None:
            incomplete = True
            add(Issue("line_incomplete", "warning", "lines", {"position": ln.position}))

    # налог по документу ≈ сумма налогов по строкам
    line_taxes = [ln.tax_amount for ln in lines if ln.tax_amount is not None]
    if header.tax_total is not None and line_taxes:
        s = sum(line_taxes, Decimal(0))
        if abs(s - header.tax_total) > tolerance(minor_units, len(line_taxes)):
            add(
                Issue(
                    "tax_total_mismatch",
                    "warning",
                    "tax_total",
                    {"expected": _s(s, minor_units), "actual": _s(header.tax_total, minor_units)},
                )
            )

    # итог ≈ Σ сумм строк ИЛИ Σ сумм + Σ налогов (какой именно режим цен — нам неизвестно)
    if header.total_amount is not None and lines and not incomplete:
        net = sum((ln.amount or Decimal(0) for ln in lines), Decimal(0))
        gross = net + sum(line_taxes, Decimal(0))
        tol = tolerance(minor_units, len(lines))
        if abs(net - header.total_amount) > tol and abs(gross - header.total_amount) > tol:
            add(
                Issue(
                    "total_mismatch",
                    "warning",
                    "total_amount",
                    {
                        "net": _s(net, minor_units),
                        "gross": _s(gross, minor_units),
                        "actual": _s(header.total_amount, minor_units),
                    },
                )
            )
    return issues
