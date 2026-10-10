"""Единый механизм списков: пагинация, поиск, сортировка (ТЗ §4, §15).

Параметры одинаковы для всех справочников; сортировка — только по явному белому списку
колонок, порядок стабилен (добавляется уникальный ключ), LIKE-символы в поиске экранируются.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import SQLColumnExpression

from app.platform.errors import AppError

MAX_PAGE_SIZE = 100


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class ListQuery:
    page: int
    page_size: int
    q: str | None
    sort: str | None
    order: Literal["asc", "desc"] | None  # None -> порядок по умолчанию задаёт сам список


def list_query(
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[str | None, Query(max_length=30)] = None,
    order: Annotated[Literal["asc", "desc"] | None, Query()] = None,
) -> ListQuery:
    term = q.strip() if q else None
    return ListQuery(page, page_size, term or None, sort, order)


ListParams = Annotated[ListQuery, Depends(list_query)]


def like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search(stmt: Select[Any], term: str | None, *columns: SQLColumnExpression[Any]) -> Select[Any]:
    if not term:
        return stmt
    pattern = like_pattern(term)
    return stmt.where(or_(*[c.ilike(pattern, escape="\\") for c in columns]))


def paginate(
    db: Session,
    stmt: Select[Any],
    lq: ListQuery,
    *,
    sort_map: dict[str, SQLColumnExpression[Any]],
    default_sort: str,
    id_column: SQLColumnExpression[Any],
    default_order: Literal["asc", "desc"] = "asc",
) -> tuple[list[Any], int]:
    key = lq.sort or default_sort
    column = sort_map.get(key)
    if column is None:
        raise AppError(
            422,
            "validation_error",
            "Недопустимое поле сортировки.",
            [{"field": "sort", "code": "invalid_sort", "message": "Недопустимое поле сортировки."}],
        )
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    ordering = column.asc() if (lq.order or default_order) == "asc" else column.desc()
    rows = db.scalars(
        stmt.order_by(ordering, id_column).limit(lq.page_size).offset((lq.page - 1) * lq.page_size)
    ).all()
    return list(rows), int(total)
