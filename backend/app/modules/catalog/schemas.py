import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    model_validator,
)

from app.platform.validators import reject_non_plain_number, validate_tin

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


# ---------- товары и услуги ----------

_BARCODE_RE = re.compile(r"^[0-9A-Za-z._-]{4,32}$")


def _barcode(v: str) -> str:
    if not _BARCODE_RE.match(v):
        raise ValueError("barcode_invalid")
    return v


def _no_float(v: Any) -> Any:
    """Деньги/количества: только строка с обычной десятичной записью или целое (без float и «1e3»)."""
    return reject_non_plain_number(v, "price_invalid")


def _fits_numeric(v: Decimal) -> Decimal:
    """NUMERIC(20,4) хранит до 16 цифр целой части; больше — ошибка валидации, а не 500."""
    if v >= Decimal(10) ** 16:
        raise ValueError("too_large")
    return v


Barcode = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_barcode)]
Money = Annotated[
    Decimal,
    BeforeValidator(_no_float),
    Field(ge=0, max_digits=20, decimal_places=4, allow_inf_nan=False),
    AfterValidator(_fits_numeric),
]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
CurrencyCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=3, max_length=3)
]
Ikpu = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=50),
    AfterValidator(_blank_to_none),
]
Description = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=2000),
    AfterValidator(_blank_to_none),
]


def _unique_barcodes(values: list[str] | None) -> list[str] | None:
    if values is not None and len({v.lower() for v in values}) != len(values):
        raise ValueError("barcodes_duplicate")
    return values


Barcodes = Annotated[list[Barcode], Field(max_length=20), AfterValidator(_unique_barcodes)]


class ProductCreate(BaseModel):
    kind: Literal["goods", "service"]
    sku: Sku
    name: Text200
    unit_id: uuid.UUID
    price: Money | None = None
    price_currency: CurrencyCode | None = None
    ikpu: Ikpu = None
    description: Description = None
    barcodes: Barcodes = Field(default_factory=list)

    @model_validator(mode="after")
    def _price_pair(self) -> "ProductCreate":
        if self.price is not None and self.price_currency is None:
            raise ValueError("price_currency_required")
        if self.price is None and self.price_currency is not None:
            raise ValueError("price_required")
        return self


class ProductUpdate(BaseModel):
    """Частичное обновление. Цена и валюта меняются вместе (оба поля или оба null)."""

    kind: Literal["goods", "service"] | None = None
    sku: Sku | None = None
    name: Text200 | None = None
    unit_id: uuid.UUID | None = None
    price: Money | None = None
    price_currency: CurrencyCode | None = None
    ikpu: Ikpu = None
    description: Description = None
    barcodes: Barcodes | None = None


class ProductUnit(BaseModel):
    id: uuid.UUID
    code: str
    name_uz: str | None
    name_ru: str | None
    name_en: str | None


class ProductOut(BaseModel):
    id: uuid.UUID
    kind: str
    sku: str
    name: str
    unit: ProductUnit
    price: str | None  # точная строка с числом знаков валюты, не float
    price_currency: str | None
    ikpu: str | None
    description: str | None
    barcodes: list[str]
    archived: bool
    created_at: datetime
    updated_at: datetime
