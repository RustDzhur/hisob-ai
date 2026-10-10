import re
import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, StringConstraints, model_validator

_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,15}$")


def _code(v: str) -> str:
    v = v.strip()
    if not _CODE_RE.match(v):
        raise ValueError("unit_code_invalid")
    return v


def _blank_to_none(v: str | None) -> str | None:
    return v if v else None


Name = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=100),
    AfterValidator(_blank_to_none),
]


class UnitCreate(BaseModel):
    code: Annotated[str, AfterValidator(_code)]
    name_uz: Name = None
    name_ru: Name = None
    name_en: Name = None

    @model_validator(mode="after")
    def _has_name(self) -> "UnitCreate":
        if not (self.name_uz or self.name_ru or self.name_en):
            raise ValueError("unit_name_required")
        return self


class UnitUpdate(BaseModel):
    """Частичное обновление: меняются только переданные поля. Код неизменяем."""

    name_uz: Name = None
    name_ru: Name = None
    name_en: Name = None
    is_active: bool | None = None


class UnitOut(BaseModel):
    id: uuid.UUID
    code: str
    name_uz: str | None
    name_ru: str | None
    name_en: str | None
    is_system: bool
    is_active: bool
