import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _blank_to_none(v: str | None) -> str | None:
    return v if v else None


Address = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=500),
    AfterValidator(_blank_to_none),
]
Notes = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=2000),
    AfterValidator(_blank_to_none),
]


class WarehouseCreate(BaseModel):
    name: Name
    address: Address = None
    notes: Notes = None


class WarehouseUpdate(BaseModel):
    """Частичное обновление: меняются только переданные поля."""

    name: Name | None = None
    address: Address = None
    notes: Notes = None


class WarehouseOut(BaseModel):
    id: uuid.UUID
    name: str
    address: str | None
    notes: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
