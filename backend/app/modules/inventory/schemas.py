import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
)

from app.platform.validators import reject_non_plain_number

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


# ---------------- склад: операции, остатки, движения (срез 4.1) ----------------

OpKind = Literal["receipt", "issue", "transfer", "supplier_return", "customer_return"]
# инвентаризационная корректировка появится в срезе 4.3


def _no_float(v: Any) -> Any:
    """Деньги/количества: только строка с обычной десятичной записью или целое (без float и «1e3»)."""
    return reject_non_plain_number(v, "number_invalid")


def _fits(v: Decimal) -> Decimal:
    if v >= Decimal(10) ** 14:
        raise ValueError("too_large")
    return v


PositiveQty = Annotated[
    Decimal,
    BeforeValidator(_no_float),
    Field(gt=0, max_digits=20, decimal_places=6, allow_inf_nan=False),
    AfterValidator(_fits),
]
Reference = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=100),
    AfterValidator(_blank_to_none),
]


class StockLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: PositiveQty


class OperationCreate(BaseModel):
    kind: OpKind
    op_date: date
    warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID | None = None
    reference: Reference = None
    notes: Notes = None
    lines: Annotated[list[StockLineIn], Field(min_length=1, max_length=500)]


class OperationUpdate(BaseModel):
    """Частичное обновление черновика; строки (если переданы) заменяются целиком."""

    expected_version: int = Field(ge=1)
    op_date: date | None = None
    warehouse_id: uuid.UUID | None = None
    to_warehouse_id: uuid.UUID | None = None
    reference: Reference = None
    notes: Notes = None
    lines: Annotated[list[StockLineIn], Field(min_length=1, max_length=500)] | None = None


class OperationAction(BaseModel):
    expected_version: int = Field(ge=1)


class ReverseIn(BaseModel):
    """Сторно проведённой операции. Дата по умолчанию — сегодня."""

    expected_version: int = Field(ge=1)
    op_date: date | None = None


class FromDocumentIn(BaseModel):
    """Складская операция по утверждённому документу; направление выбирает человек."""

    document_id: uuid.UUID
    kind: Literal["receipt", "issue", "supplier_return", "customer_return"]
    warehouse_id: uuid.UUID
    op_date: date | None = None


class DocRef(BaseModel):
    id: uuid.UUID
    number: str | None
    doc_type: str


class NameRef(BaseModel):
    id: uuid.UUID
    name: str


class UnitRef(BaseModel):
    code: str
    name_uz: str | None
    name_ru: str | None
    name_en: str | None


class StockLineOut(BaseModel):
    position: int
    product_id: uuid.UUID
    sku: str
    name: str
    unit: UnitRef
    quantity: str


class OperationOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    op_date: date
    warehouse: NameRef
    to_warehouse: NameRef | None
    reference: str | None
    notes: str | None
    version: int
    source_document: DocRef | None
    reverses_id: uuid.UUID | None
    reversed_by_id: uuid.UUID | None
    lines: list[StockLineOut]
    created_by_name: str | None
    posted_by_name: str | None
    posted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OperationListItem(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    op_date: date
    warehouse_name: str
    to_warehouse_name: str | None
    reference: str | None
    lines_count: int
    is_reversal: bool
    reversed: bool
    created_at: datetime


class BalanceOut(BaseModel):
    warehouse: NameRef
    product_id: uuid.UUID
    sku: str
    name: str
    unit: UnitRef
    quantity: str


class MovementOut(BaseModel):
    id: uuid.UUID
    operation_id: uuid.UUID
    kind: str
    reference: str | None
    warehouse: NameRef
    product_id: uuid.UUID
    sku: str
    name: str
    unit: UnitRef
    quantity_delta: str
    op_date: date
    occurred_at: datetime


class ReconciliationRow(BaseModel):
    warehouse: NameRef
    product_id: uuid.UUID
    sku: str
    name: str
    balance: str
    movements_sum: str


class ReconciliationOut(BaseModel):
    ok: bool
    checked_pairs: int
    mismatches: list[ReconciliationRow]


class StockSettings(BaseModel):
    negative_stock_policy: Literal["forbid", "allow"]
