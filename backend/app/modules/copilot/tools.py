"""Инструменты Copilot: только чтение. Каждый вызывается от имени спросившего и проверяет его права.

Нет SQL, нет записи, нет секретов. Результат — JSON с источником и пометкой неполноты; текст третьих лиц
в результатах — данные, а не инструкции (это сказано модели в системной подсказке).
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounting import service as accounting
from app.modules.bank import reconcile, reports
from app.modules.bank.models import BankMatch
from app.modules.catalog.models import Counterparty
from app.modules.documents import service as documents
from app.modules.documents.models import Document
from app.modules.identity.service import Principal
from app.modules.inventory import stock
from app.modules.tenancy.access import authorize
from app.platform.errors import AppError
from app.platform.listing import ListQuery

MAX_TOOL_CHARS = 12_000
STOCK_ROWS = 30
DOC_ROWS = 20
UNPAID_ROWS = 30
TB_ROWS = 40


@dataclass(frozen=True)
class Ctx:
    db: Session
    principal: Principal
    org_id: uuid.UUID


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StockArgs(_Args):
    query: str | None = Field(
        None, max_length=100, description="Часть названия или артикула товара"
    )
    warehouse_id: uuid.UUID | None = Field(None, description="Идентификатор склада")
    low_only: bool = Field(False, description="Только позиции ниже минимального запаса")


class SearchDocsArgs(_Args):
    query: str | None = Field(None, max_length=100, description="Номер, контрагент или имя файла")
    status: Literal[
        "active", "needs_review", "approved", "rejected", "duplicate", "archived", "all"
    ] = "active"
    doc_type: str | None = Field(None, max_length=20)
    date_from: date | None = None
    date_to: date | None = None


class UnpaidArgs(_Args):
    min_age_days: int = Field(
        0, ge=0, le=3650, description="Только документы старше N дней от даты документа"
    )


class BankSummaryArgs(_Args):
    date_from: date
    date_to: date
    account_id: uuid.UUID | None = None


class ExplainArgs(_Args):
    transaction_id: uuid.UUID


class TrialArgs(_Args):
    date_from: date
    date_to: date
    currency: str | None = Field(None, min_length=3, max_length=3)


def _lq(q: str | None, size: int) -> ListQuery:
    return ListQuery(page=1, page_size=size, q=q or None, sort=None, order=None)


def _stock(c: Ctx, a: StockArgs) -> dict[str, Any]:
    page = stock.list_balances(
        c.db,
        c.principal,
        c.org_id,
        _lq(a.query, STOCK_ROWS),
        warehouse_id=a.warehouse_id,
        include_zero=False,
        low_only=a.low_only,
    )
    return {
        "rows": [r.model_dump(mode="json") for r in page.items],
        "total": page.total,
        "truncated": page.total > len(page.items),
        "note": "Количества в единицах товара; резерв уменьшает свободный остаток. Нулевые остатки не показаны.",
    }


def _search_docs(c: Ctx, a: SearchDocsArgs) -> dict[str, Any]:
    page = documents.list_documents(
        c.db,
        c.principal,
        c.org_id,
        _lq(a.query, DOC_ROWS),
        status=a.status,
        doc_type=a.doc_type,
        counterparty_id=None,
        date_from=a.date_from,
        date_to=a.date_to,
    )
    return {
        "rows": [r.model_dump(mode="json") for r in page.items],
        "total": page.total,
        "truncated": page.total > len(page.items),
    }


def _unpaid(c: Ctx, a: UnpaidArgs) -> dict[str, Any]:
    authorize(c.db, c.principal, c.org_id, "documents.read")
    authorize(c.db, c.principal, c.org_id, "bank.read")
    paid = (
        select(BankMatch.document_id.label("d"), func.sum(BankMatch.amount).label("m"))
        .where(BankMatch.organization_id == c.org_id, BankMatch.status == "CONFIRMED")
        .group_by(BankMatch.document_id)
        .subquery()
    )
    remaining = Document.total_amount - func.coalesce(paid.c.m, 0)
    today = date.today()
    stmt = (
        select(Document, func.coalesce(paid.c.m, 0), Counterparty.name)
        .outerjoin(paid, paid.c.d == Document.id)
        .outerjoin(
            Counterparty,
            (Counterparty.id == Document.counterparty_id)
            & (Counterparty.organization_id == Document.organization_id),
        )
        .where(
            Document.organization_id == c.org_id,
            Document.status == "APPROVED",
            Document.total_amount.is_not(None),
            remaining > 0,
        )
    )
    if a.min_age_days:
        stmt = stmt.where(Document.doc_date <= today - timedelta(days=a.min_age_days))
    total = int(c.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = c.db.execute(
        stmt.order_by(Document.doc_date.asc().nulls_last(), Document.id).limit(UNPAID_ROWS)
    ).all()
    out = []
    for d, m, cp in rows:
        total_amt = Decimal(d.total_amount)
        out.append(
            {
                "document_id": str(d.id),
                "number": d.number,
                "doc_type": d.doc_type,
                "doc_date": d.doc_date.isoformat() if d.doc_date else None,
                "counterparty": cp,
                "currency": d.currency,
                "total": format(total_amt, "f"),
                "paid_by_bank": format(Decimal(m), "f"),
                "remaining": format(total_amt - Decimal(m), "f"),
                "age_days": (today - d.doc_date).days if d.doc_date else None,
            }
        )
    return {
        "rows": out,
        "total": total,
        "truncated": total > len(out),
        "as_of": today.isoformat(),
        "note": (
            "Неоплаченным считается остаток документа после подтверждённых банковских сопоставлений. "
            "Сроки оплаты и направление (мы должны / нам должны) в системе не хранятся, поэтому «просрочено» "
            "определить нельзя — показан возраст от даты документа. Суммы в разных валютах не складывать."
        ),
    }


def _bank_summary(c: Ctx, a: BankSummaryArgs) -> dict[str, Any]:
    r = reports.summary(
        c.db,
        c.principal,
        c.org_id,
        date_from=a.date_from,
        date_to=a.date_to,
        account_id=a.account_id,
    )
    return r.model_dump(mode="json")


def _explain(c: Ctx, a: ExplainArgs) -> dict[str, Any]:
    r = reconcile.transaction_detail(c.db, c.principal, c.org_id, a.transaction_id)
    d = r.model_dump(mode="json")
    d["candidates"] = d["candidates"][:5]
    return d


def _trial(c: Ctx, a: TrialArgs) -> dict[str, Any]:
    r = accounting.trial_balance(
        c.db, c.principal, c.org_id, date_from=a.date_from, date_to=a.date_to, currency=a.currency
    )
    d = r.model_dump(mode="json")
    total = len(d["rows"])
    d["rows"] = d["rows"][:TB_ROWS]
    d["truncated"] = total > TB_ROWS
    return d


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    run: Callable[[Ctx, Any], dict[str, Any]]

    def spec(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "parameters": schema}


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "stock_balances",
            "Остатки товаров по складам: количество, резерв, свободно, минимальный запас.",
            StockArgs,
            _stock,
        ),
        Tool(
            "search_documents",
            "Поиск документов (счета, накладные и т. п.) по тексту, статусу, типу и датам.",
            SearchDocsArgs,
            _search_docs,
        ),
        Tool(
            "unpaid_documents",
            "Утверждённые документы с неоплаченным остатком по банковским сопоставлениям.",
            UnpaidArgs,
            _unpaid,
        ),
        Tool(
            "bank_summary",
            "Сводка по банковским счетам за период: поступления, списания, сверено и не сверено.",
            BankSummaryArgs,
            _bank_summary,
        ),
        Tool(
            "explain_reconciliation",
            "Объяснение сверки банковской операции: сопоставления и предложения с причинами.",
            ExplainArgs,
            _explain,
        ),
        Tool(
            "trial_balance",
            "Оборотно-сальдовая ведомость за период в одной валюте.",
            TrialArgs,
            _trial,
        ),
    )
}


def specs() -> list[dict[str, Any]]:
    return [t.spec() for t in TOOLS.values()]


def _wrap(name: str, data: dict[str, Any]) -> str:
    return json.dumps({"source": name, "data": data}, ensure_ascii=False, default=str)


def _clip(name: str, data: dict[str, Any]) -> str:
    text = _wrap(name, data)
    if len(text) <= MAX_TOOL_CHARS:
        return text
    rows = data.get("rows")
    if isinstance(rows, list):
        keep = list(rows)
        while keep and len(_wrap(name, {**data, "rows": keep, "truncated": True})) > MAX_TOOL_CHARS:
            keep = keep[: len(keep) // 2]
        return _wrap(name, {**data, "rows": keep, "truncated": True})
    return json.dumps({"source": name, "error": "result_too_large", "truncated": True})


def execute(c: Ctx, name: str, raw_args: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Возвращает (текст результата для модели, запись для журнала). Исключения наружу не выходят."""
    trace: dict[str, Any] = {"tool": name}
    tool = TOOLS.get(name)
    if tool is None:
        trace["error"] = "unknown_tool"
        return json.dumps({"error": "unknown_tool"}), trace
    try:
        data = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        if not isinstance(data, dict):
            raise ValueError("args must be an object")
        args = tool.args.model_validate(data)
    except (ValueError, ValidationError):
        trace["error"] = "invalid_arguments"
        return json.dumps({"error": "invalid_arguments"}), trace
    trace["args"] = args.model_dump(mode="json")
    try:
        payload = tool.run(c, args)
        out = _clip(name, payload)
        trace["rows"] = (
            len(payload.get("rows", [])) if isinstance(payload.get("rows"), list) else None
        )
        trace["truncated"] = bool(payload.get("truncated"))
        return out, trace
    except AppError as exc:
        c.db.rollback()
        trace["error"] = exc.code
        return json.dumps({"source": name, "error": exc.code}), trace
