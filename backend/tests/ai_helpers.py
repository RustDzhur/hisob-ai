"""Помощники тестов распознавания: ответы mock-провайдера и запуск задачи воркера напрямую."""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.documents.extraction import runner
from app.modules.documents.extraction.providers import MockProvider, set_provider_for_tests
from app.modules.documents.extraction.schema import HEADER_FIELDS, LINE_FIELDS


def f(
    value: str | None = None, conf: float | None = 0.9, evidence: str | None = None
) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": conf if value is not None else None,
        "evidence": evidence if evidence is not None else (f"…{value}…" if value else None),
    }


def line(
    desc: str | None,
    qty: str | None = None,
    unit: str | None = None,
    price: str | None = None,
    amount: str | None = None,
    article: str | None = None,
    discount: str | None = None,
    tax: str | None = None,
) -> dict[str, Any]:
    d = {k: f() for k in LINE_FIELDS}
    d.update(
        description=f(desc),
        quantity=f(qty),
        unit=f(unit),
        unit_price=f(price),
        amount=f(amount),
        article=f(article),
        discount=f(discount),
        tax_amount=f(tax),
    )
    return d


def payload(lines: list[dict[str, Any]] | None = None, **fields: str | None) -> dict[str, Any]:
    d: dict[str, Any] = {k: f() for k in HEADER_FIELDS}
    for k, v in fields.items():
        d[k] = f(v)
    d["lines"] = lines or []
    return d


def good_payload(**over: str | None) -> dict[str, Any]:
    base: dict[str, str | None] = dict(
        doc_type="invoice",
        number="INV-100",
        date="2026-10-01",
        currency="UZS",
        total_amount="300",
        tax_total=None,
        seller_name="ООО Поставщик Альфа",
        seller_tin="123456789",
        buyer_name="ООО Наша Компания",
        buyer_tin="999888777",
    )
    base.update(over)
    return payload(
        [
            line("Молоко 3%", "2", "шт", "100", "200", article="MILK-1"),
            line("Хлеб белый", "10", "кг", "10", "100"),
        ],
        **base,
    )


def set_runtime(
    engine: Engine, provider: str = "mock", model: str = "mock-1", configured: bool = True
) -> None:
    with engine.begin() as c:
        c.execute(text("DELETE FROM ai_runtime"))
        c.execute(
            text("INSERT INTO ai_runtime (id, provider, model, configured) VALUES (1, :p, :m, :c)"),
            {"p": provider, "m": model, "c": configured},
        )


def run_job(job_id: str, org_id: str, final: bool = True) -> None:
    runner.run_job(uuid.UUID(job_id), uuid.UUID(org_id), final_attempt=final)


@pytest.fixture
def mock_ai(admin_engine: Engine) -> Iterator[Callable[[Any], MockProvider]]:
    """Фабрика mock-провайдера: регистрирует его и отмечает AI как настроенный. Только для тестов."""
    created: list[MockProvider] = []

    def make(handler: Any) -> MockProvider:
        p = MockProvider(handler)
        set_provider_for_tests(p)
        set_runtime(admin_engine)
        created.append(p)
        return p

    yield make
    set_provider_for_tests(None)
