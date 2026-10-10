"""Метрики сравнения провайдеров должны быть верны сами по себе: иначе сравнение ничего не значит."""

from typing import Any

from app.modules.documents.extraction.schema import ExtractionOut
from scripts.eval_providers import Tally, norm, score
from tests.ai_helpers import line, payload


def out(**fields: str | None) -> ExtractionOut:
    return ExtractionOut.model_validate(
        payload([line("Молоко", "2", "шт", "100", "200")], **fields)
    )


def expected(**over: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        k: None
        for k in (
            "doc_type",
            "number",
            "date",
            "currency",
            "total_amount",
            "tax_total",
            "seller_name",
            "seller_tin",
            "buyer_name",
            "buyer_tin",
        )
    }
    e["lines"] = [
        {
            "description": "Молоко",
            "quantity": "2",
            "unit": "шт",
            "unit_price": "100",
            "amount": "200",
        }
    ]
    e.update(over)
    return e


def test_norm_makes_equivalent_forms_equal() -> None:
    assert norm("total_amount", "1250.50") == norm("total_amount", "1250.5")
    assert norm("date", "2026-10-01") is not None
    assert norm("seller_tin", "123 456 789") == "123456789"
    assert norm("number", " inv-1 ") == "INV-1"
    assert norm("number", "") is None


def test_tally_classes_and_high_confidence_errors() -> None:
    t = Tally()
    assert t.add("a", "a", 0.9) == "correct"
    assert t.add(None, None, None) == "correct_null"
    assert t.add("a", "b", 0.95) == "wrong"
    assert t.add("a", None, None) == "missed"
    assert t.add(None, "x", 0.5) == "hallucinated"
    assert (t.total, t.errors, t.high_conf_errors) == (5, 3, 1)


def test_score_perfect_and_flawed_documents() -> None:
    t = Tally()
    assert score(out(), expected(), t) == [] and t.errors == 0
    t2 = Tally()
    problems = score(out(number="99", total_amount="5"), expected(number="1"), t2)
    assert t2.wrong == 1 and t2.hallucinated == 1
    assert any("number:wrong" in p for p in problems) and any(
        "total_amount:hallucinated" in p for p in problems
    )


def test_missing_and_extra_lines_are_errors() -> None:
    t = Tally()
    e = expected()
    e["lines"].append(
        {"description": "Хлеб", "quantity": "1", "unit": "шт", "unit_price": "10", "amount": "10"}
    )
    problems = score(out(), e, t)
    assert t.missed == 5 and any("line 2" in p for p in problems)
    t2 = Tally()
    e2 = expected()
    e2["lines"] = []
    score(out(), e2, t2)
    assert t2.hallucinated == 5
