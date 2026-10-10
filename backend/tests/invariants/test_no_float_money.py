"""Деньги — только Decimal/NUMERIC. Пока денежных полей нет; тест не даст добавить float позже."""

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
MONEY_WORDS = re.compile(r"(amount|price|total|cost|balance|sum|qty|quantity)\w*\s*:\s*float", re.I)


def test_no_float_annotations_for_money_fields() -> None:
    offenders = [
        str(p) for p in APP.rglob("*.py") if MONEY_WORDS.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
