import uuid
from types import SimpleNamespace
from typing import Any

from app.modules.documents.extraction.matching import UnitResolver


def unit(code: str, uz: str | None, ru: str | None, en: str | None) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), code=code, name_uz=uz, name_ru=ru, name_en=en)


def test_unit_resolver_exact_across_languages() -> None:
    pcs, kg = unit("pcs", "dona", "шт", "pcs"), unit("kg", "kg", "кг", "kg")
    r = UnitResolver([pcs, kg])
    for text in ("шт", "ШТ.", " dona ", "PCS", "pcs."):
        assert r.resolve(text) == pcs.id, text
    assert r.resolve("кг") == kg.id
    assert r.resolve("коробка") is None and r.resolve(None) is None and r.resolve("") is None
