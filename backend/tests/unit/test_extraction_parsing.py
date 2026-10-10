from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.modules.documents.extraction import schema
from app.modules.documents.extraction.parsing import (
    digits_only,
    jaccard,
    normalize_name,
    parse_date,
    parse_decimal,
    tokens,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1 250 000,50", D("1250000.50")),
        ("1250000.5", D("1250000.5")),
        ("1.250,50", D("1250.50")),
        ("1,250.50", D("1250.50")),
        ("200", D(200)),
        ("0", D(0)),
        ("-5", D(-5)),
        ("1 000", D(1000)),
        ("12,5", D("12.5")),
    ],
)
def test_parse_decimal_ok(raw: str, expected: D) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", None, "abc", "1e5", "12.5.3", "UZS 100", "٣", "1,2,3", "∞", "NaN"]
)
def test_parse_decimal_rejects_garbage(raw: str | None) -> None:
    assert parse_decimal(raw) is None


def test_parse_decimal_decimals_limit() -> None:
    assert parse_decimal("10.005", max_decimals=2) is None
    assert parse_decimal("10.50", max_decimals=2) == D("10.50")
    assert parse_decimal("10.500", max_decimals=2) == D("10.5")  # хвостовые нули — не лишние знаки


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-10-01", date(2026, 10, 1)),
        ("01.10.2026", date(2026, 10, 1)),
        ("01/10/2026", date(2026, 10, 1)),
        ("2026.10.01", date(2026, 10, 1)),
    ],
)
def test_parse_date(raw: str, expected: date) -> None:
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "yesterday", "32.01.2026", "1999-01-01", "2101-01-01", "10/01/26"]
)
def test_parse_date_rejects(raw: str | None) -> None:
    assert parse_date(raw) is None


def test_names_and_tokens() -> None:
    assert normalize_name('ООО "Ромашка-Плюс"') == "ромашка плюс"
    assert normalize_name("Ромашка Плюс MCHJ") == "ромашка плюс"
    assert normalize_name(None) == ""
    assert jaccard(tokens('ООО "Ромашка Плюс"'), tokens("Ромашка плюс ООО")) == 1.0
    assert 0.3 < jaccard(tokens("Ромашка Плюс Трейд"), tokens("Ромашка Плюс")) < 1.0
    assert jaccard(set(), {"a"}) == 0.0
    assert digits_only("ИНН: 123 456-789") == "123456789" and digits_only("нет") is None


def _field(v: str | None = None, c: float | None = None, e: str | None = None) -> dict[str, object]:
    return {"value": v, "confidence": c, "evidence": e}


def _doc(**over: object) -> dict[str, object]:
    d: dict[str, object] = {f: _field() for f in schema.HEADER_FIELDS}
    d["lines"] = []
    d.update(over)
    return d


def test_schema_is_strict_and_complete() -> None:
    js = schema.json_schema()
    assert js["additionalProperties"] is False
    assert set(js["required"]) == {*schema.HEADER_FIELDS, "lines"} == set(js["properties"])
    line = js["properties"]["lines"]["items"]
    assert (
        set(line["required"]) == set(schema.LINE_FIELDS) and line["additionalProperties"] is False
    )
    assert None in js["properties"]["doc_type"]["properties"]["value"]["enum"]
    for name in (*schema.HEADER_FIELDS,):
        f = js["properties"][name]
        assert (
            f["required"] == ["value", "confidence", "evidence"]
            and f["additionalProperties"] is False
        )


def test_output_model_validation() -> None:
    ok = schema.ExtractionOut.model_validate(_doc(number=_field(" 77 ", 0.9, "  No 77 ")))
    assert ok.number.value == "77" and ok.number.evidence == "No 77" and ok.number.confidence == 0.9
    assert (
        schema.ExtractionOut.model_validate(_doc(number=_field("x", 7))).number.confidence is None
    )  # вне 0..1
    assert schema.ExtractionOut.model_validate(_doc(number=_field("   "))).number.value is None
    with pytest.raises(ValidationError):  # лишний ключ — отказ (в т.ч. «инструкции» от модели)
        schema.ExtractionOut.model_validate(_doc(approve=True))
    with pytest.raises(ValidationError):
        schema.ExtractionOut.model_validate({k: v for k, v in _doc().items() if k != "number"})
    with pytest.raises(ValidationError):  # значение должно быть строкой, а не числом
        schema.ExtractionOut.model_validate(
            _doc(total_amount={"value": 12.5, "confidence": 1, "evidence": None})
        )
    with pytest.raises(ValidationError):
        schema.ExtractionOut.model_validate(_doc(lines=[{"description": _field("x")}]))
