import re
import uuid
from datetime import datetime
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, StringConstraints, model_validator

from app.platform.validators import validate_tin

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


# ---------- контрагенты ----------

_PHONE_RE = re.compile(r"^[0-9+()\-. ]{5,32}$")


def _phone(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _PHONE_RE.match(v):
        raise ValueError("phone_invalid")
    return v


def _email(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    try:
        validate_email(v, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError("email_invalid") from exc
    return v.lower()


Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OptText = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=500),
    AfterValidator(_blank_to_none),
]
OptPhone = Annotated[
    str | None, StringConstraints(strip_whitespace=True, max_length=32), AfterValidator(_phone)
]
OptEmail = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=200),
    AfterValidator(_email),
]
OptTin = Annotated[str | None, AfterValidator(validate_tin)]


class CounterpartyCreate(BaseModel):
    name: Text200
    tin: OptTin = None
    is_customer: bool = False
    is_supplier: bool = False
    address: OptText = None
    phone: OptPhone = None
    email: OptEmail = None
    notes: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, max_length=2000),
        AfterValidator(_blank_to_none),
    ] = None


class CounterpartyUpdate(BaseModel):
    """Частичное обновление: меняются только переданные поля."""

    name: Text200 | None = None
    tin: OptTin = None
    is_customer: bool | None = None
    is_supplier: bool | None = None
    address: OptText = None
    phone: OptPhone = None
    email: OptEmail = None
    notes: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, max_length=2000),
        AfterValidator(_blank_to_none),
    ] = None


class CounterpartyOut(BaseModel):
    id: uuid.UUID
    name: str
    tin: str | None
    is_customer: bool
    is_supplier: bool
    address: str | None
    phone: str | None
    email: str | None
    notes: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
