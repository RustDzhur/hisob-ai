import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, StringConstraints

from app.modules.catalog.schemas import CurrencyCode, Money
from app.modules.documents.models import DOC_TYPES

DocType = Literal[
    "invoice",
    "esf",
    "waybill",
    "ttn",
    "act",
    "contract",
    "receipt",
    "statement",
    "payment_order",
    "return",
    "other",
]
assert set(DOC_TYPES) == set(DocType.__args__)  # type: ignore[attr-defined]


def _blank_to_none(v: str | None) -> str | None:
    return v if v else None


def _no_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("number_invalid")
    return v


def _fits(v: Decimal) -> Decimal:
    if v >= Decimal(10) ** 14:
        raise ValueError("too_large")
    return v


Qty = Annotated[
    Decimal,
    BeforeValidator(_no_float),
    Field(ge=0, max_digits=20, decimal_places=6, allow_inf_nan=False),
    AfterValidator(_fits),
]
Number = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=100),
    AfterValidator(_blank_to_none),
]
Notes = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=2000),
    AfterValidator(_blank_to_none),
]
Desc = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class LineIn(BaseModel):
    description: Desc
    product_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    quantity: Qty | None = None
    unit_price: Money | None = None
    discount: Money | None = None
    tax_amount: Money | None = None
    amount: Money | None = None


class DocumentUpdate(BaseModel):
    """Частичное обновление шапки; строки (если переданы) заменяются целиком. expected_version обязателен."""

    expected_version: int = Field(ge=1)
    doc_type: DocType | None = None
    number: Number = None
    doc_date: date | None = None
    counterparty_id: uuid.UUID | None = None
    currency: CurrencyCode | None = None
    total_amount: Money | None = None
    tax_total: Money | None = None
    notes: Notes = None
    lines: Annotated[list[LineIn], Field(max_length=500)] | None = None


class ActionIn(BaseModel):
    expected_version: int = Field(ge=1)


class ApproveIn(ActionIn):
    # коды предупреждений, которые проверяющий видел и принял
    acknowledged: list[str] = Field(default_factory=list, max_length=50)


class RejectIn(ActionIn):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DuplicateIn(ActionIn):
    original_id: uuid.UUID


class IssueOut(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    field: str | None
    params: dict[str, Any]


class CounterpartyRef(BaseModel):
    id: uuid.UUID
    name: str
    tin: str | None
    archived: bool


class FileOut(BaseModel):
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str
    inline: bool


class LineOut(BaseModel):
    position: int
    description: str
    product_id: uuid.UUID | None
    product_sku: str | None
    unit_id: uuid.UUID | None
    unit_code: str | None
    quantity: str | None
    unit_price: str | None
    discount: str | None
    tax_amount: str | None
    amount: str | None


class ExtractedFieldOut(BaseModel):
    path: str
    value: str | None
    confidence: float | None
    evidence: str | None
    validation: str
    applied: bool


class ExtractionJobOut(BaseModel):
    id: uuid.UUID
    status: str  # queued | running | succeeded | failed | cancelled
    provider: str
    model: str
    mock: bool  # результаты mock-провайдера не являются распознаванием; в UI всегда помечаются
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class ExtractionView(BaseModel):
    job: ExtractionJobOut | None = None
    fields: list[ExtractedFieldOut] = Field(default_factory=list)
    counterparty_candidates: list[dict[str, Any]] = Field(default_factory=list)
    line_matches: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class AIStatusOut(BaseModel):
    configured: bool
    provider: str | None
    model: str | None
    mock: bool


class DocumentOut(BaseModel):
    id: uuid.UUID
    doc_type: str
    status: str
    number: str | None
    doc_date: date | None
    counterparty: CounterpartyRef | None
    currency: str | None
    total_amount: str | None
    tax_total: str | None
    notes: str | None
    file: FileOut | None
    lines: list[LineOut]
    issues: list[IssueOut]
    version: int
    extraction: ExtractionView
    duplicate_of_id: uuid.UUID | None
    rejection_reason: str | None
    approved_by_name: str | None
    approved_at: datetime | None
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime


class DocumentListItem(BaseModel):
    id: uuid.UUID
    doc_type: str
    status: str
    number: str | None
    doc_date: date | None
    counterparty_name: str | None
    currency: str | None
    total_amount: str | None
    original_name: str | None
    version: int
    created_at: datetime
