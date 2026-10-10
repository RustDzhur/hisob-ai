"""Детерминированное сопоставление результатов AI со справочниками (ТЗ §3, п. 6): с объяснением причины.

AI только предлагает; здесь нет вызовов моделей. Автоматически подставляется лишь точное совпадение
(ИНН контрагента, штрихкод/артикул товара); всё остальное — варианты для выбора человеком.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.catalog.models import Counterparty, Product, ProductIdentifier, UnitOfMeasure
from app.modules.documents.extraction.parsing import digits_only, jaccard, normalize_name, tokens

SIMILAR_THRESHOLD = 0.6
MAX_CANDIDATES = 3


@dataclass(frozen=True)
class Party:
    name: str | None
    tin: str | None  # только цифры


def _cp_candidate(c: Counterparty, reason: str, score: float, party: str) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "tin": c.tin,
        "reason": reason,
        "score": round(score, 3),
        "party": party,
        "archived": c.archived_at is not None,
    }


def match_counterparty(
    db: Session, org_id: uuid.UUID, org_tin: str | None, seller: Party, buyer: Party
) -> tuple[uuid.UUID | None, list[dict[str, Any]]]:
    """Возвращает (id для автоподстановки | None, варианты).

    Контрагент — «другая сторона»: если ИНН нашей организации совпал с покупателем, это продавец, и наоборот.
    Если ИНН организации неизвестен или не совпал ни с кем, сторону определить нельзя: показываем варианты
    по обеим сторонам, ничего не подставляя.
    """
    org = digits_only(org_tin)
    if org and buyer.tin == org:
        sides = [("seller", seller)]
    elif org and seller.tin == org:
        sides = [("buyer", buyer)]
    else:
        sides = [("seller", seller), ("buyer", buyer)]
    determined = len(sides) == 1

    all_cps = list(db.scalars(select(Counterparty).where(Counterparty.organization_id == org_id)))
    by_tin = {c.tin: c for c in all_cps if c.tin}
    candidates: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    apply_id: uuid.UUID | None = None

    for side, party in sides:
        if party.tin and party.tin in by_tin and by_tin[party.tin].id not in seen:
            c = by_tin[party.tin]
            seen.add(c.id)
            candidates.append(_cp_candidate(c, "tin_match", 1.0, side))
            if determined and c.archived_at is None:
                apply_id = c.id
        norm = normalize_name(party.name)
        if not norm:
            continue
        toks = tokens(party.name)
        scored: list[tuple[float, str, Counterparty]] = []
        for c in all_cps:
            if c.id in seen:
                continue
            if normalize_name(c.name) == norm:
                scored.append((0.9, "name_match", c))
            else:
                j = jaccard(toks, tokens(c.name))
                if j >= SIMILAR_THRESHOLD:
                    scored.append((j, "similar_name", c))
        for score, reason, c in sorted(scored, key=lambda x: -x[0])[:MAX_CANDIDATES]:
            seen.add(c.id)
            candidates.append(_cp_candidate(c, reason, score, side))
    return apply_id, candidates[: MAX_CANDIDATES * 2]


def _product_candidate(p: Product, reason: str, score: float) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "sku": str(p.sku),
        "name": p.name,
        "reason": reason,
        "score": round(score, 3),
        "archived": p.archived_at is not None,
    }


def match_product(
    db: Session, org_id: uuid.UUID, article: str | None, description: str | None
) -> tuple[uuid.UUID | None, list[dict[str, Any]]]:
    """Автоподстановка только по точному штрихкоду/артикулу активного товара; по названию — варианты."""
    candidates: list[dict[str, Any]] = []
    apply_id: uuid.UUID | None = None
    art = (article or "").strip()
    if art:
        p = db.scalar(select(Product).where(Product.organization_id == org_id, Product.sku == art))
        reason = "sku_match"
        if p is None:
            p = db.scalar(
                select(Product)
                .join(ProductIdentifier, ProductIdentifier.product_id == Product.id)
                .where(
                    Product.organization_id == org_id,
                    ProductIdentifier.kind == "barcode",
                    ProductIdentifier.value == art,
                )
            )
            reason = "barcode_match"
        if p is not None:
            candidates.append(_product_candidate(p, reason, 1.0))
            if p.archived_at is None:
                apply_id = p.id

    norm = normalize_name(description)
    if norm:
        toks = tokens(description)
        long = sorted((t for t in toks if len(t) >= 4), key=len, reverse=True)[:3]
        if long:
            stmt = (
                select(Product)
                .where(
                    Product.organization_id == org_id,
                    Product.archived_at.is_(None),
                    or_(*[Product.name.ilike(f"%{t}%") for t in long]),
                )
                .limit(100)
            )
            scored: list[tuple[float, str, Product]] = []
            for p in db.scalars(stmt):
                if any(c["id"] == str(p.id) for c in candidates):
                    continue
                if normalize_name(p.name) == norm:
                    scored.append((0.9, "name_match", p))
                else:
                    j = jaccard(toks, tokens(p.name))
                    if j >= SIMILAR_THRESHOLD:
                        scored.append((j, "similar_name", p))
            for score, reason, p in sorted(scored, key=lambda x: -x[0])[:MAX_CANDIDATES]:
                candidates.append(_product_candidate(p, reason, score))
    return apply_id, candidates[:MAX_CANDIDATES]


def unit_resolver(db: Session, org_id: uuid.UUID) -> "UnitResolver":
    units = list(
        db.scalars(
            select(UnitOfMeasure).where(
                or_(
                    UnitOfMeasure.organization_id.is_(None), UnitOfMeasure.organization_id == org_id
                ),
                UnitOfMeasure.is_active.is_(True),
            )
        )
    )
    return UnitResolver(units)


class UnitResolver:
    """Текст единицы из документа («шт.», «dona», «pcs») -> единица справочника; только точное совпадение."""

    def __init__(self, units: list[UnitOfMeasure]) -> None:
        self._index: dict[str, uuid.UUID] = {}
        for u in units:
            for key in (str(u.code), u.name_uz, u.name_ru, u.name_en):
                if key:
                    self._index.setdefault(self._norm(key), u.id)

    @staticmethod
    def _norm(s: str) -> str:
        return s.strip().strip(".").casefold()

    def resolve(self, text: str | None) -> uuid.UUID | None:
        return self._index.get(self._norm(text)) if text else None
