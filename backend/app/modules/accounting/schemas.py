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
    model_validator,
)

from app.platform.validators import reject_non_plain_number


def _blank_to_none(v: str | None) -> str | None:
    return v if v else None


def _plain(v: Any) -> Any:
    return reject_non_plain_number(v, "number_invalid")


def _fits(v: Decimal) -> Decimal:
    if v >= Decimal(10) ** 16:
        raise ValueError("too_large")
    return v


Amount = Annotated[
    Decimal,
    BeforeValidator(_plain),
    Field(ge=0, max_digits=20, decimal_places=4, allow_inf_nan=False),
    AfterValidator(_fits),
]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Notes = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=2000),
    AfterValidator(_blank_to_none),
]
Memo = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=300),
    AfterValidator(_blank_to_none),
]
Currency = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=3, max_length=3)
]


# ---------------- план счетов ----------------


class AccountCreate(BaseModel):
    code: Code
    name: Name
    parent_id: uuid.UUID | None = None
    allow_posting: bool = True
    notes: Notes = None


class AccountUpdate(BaseModel):
    code: Code | None = None
    name: Name | None = None
    parent_id: uuid.UUID | None = None
    allow_posting: bool | None = None
    notes: Notes = None


class AccountOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    parent_id: uuid.UUID | None
    parent_code: str | None
    allow_posting: bool
    notes: str | None
    archived: bool
    has_entries: bool
    created_at: datetime
    updated_at: datetime


# ---------------- периоды ----------------


class PeriodCreate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class PeriodOut(BaseModel):
    id: uuid.UUID
    start_date: date
    end_date: date
    status: str
    closed_at: datetime | None
    entries_count: int


# ---------------- проводки ----------------


class LineIn(BaseModel):
    account_id: uuid.UUID
    debit: Amount = Decimal(0)
    credit: Amount = Decimal(0)
    memo: Memo = None

    @model_validator(mode="after")
    def _one_side(self) -> "LineIn":
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("one_side_required")
        return self


class EntryCreate(BaseModel):
    entry_date: date
    currency: Currency
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    lines: Annotated[list[LineIn], Field(min_length=2, max_length=200)]


class EntryReverse(BaseModel):
    entry_date: date | None = None
    description: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)] = (
        None
    )


class LineOut(BaseModel):
    position: int
    account_id: uuid.UUID
    account_code: str
    account_name: str
    debit: str
    credit: str
    memo: str | None


class SourceDoc(BaseModel):
    id: uuid.UUID
    number: str | None
    doc_type: str


class RuleRef(BaseModel):
    id: uuid.UUID
    key: str
    version: int
    name: str


class EntryOut(BaseModel):
    id: uuid.UUID
    number: int
    entry_date: date
    currency: str
    description: str
    reverses_id: uuid.UUID | None
    reversed_by_id: uuid.UUID | None
    period_id: uuid.UUID
    created_by_name: str | None
    created_at: datetime
    total: str
    source_document: SourceDoc | None = None
    rule: RuleRef | None = None
    lines: list[LineOut]


class EntryListItem(BaseModel):
    id: uuid.UUID
    number: int
    entry_date: date
    currency: str
    description: str
    total: str
    is_reversal: bool
    reversed: bool


# ---------------- отчёты ----------------


class TrialRow(BaseModel):
    account_id: uuid.UUID
    code: str
    name: str
    opening_debit: str
    opening_credit: str
    turnover_debit: str
    turnover_credit: str
    closing_debit: str
    closing_credit: str


class TrialBalanceOut(BaseModel):
    currency: str
    date_from: date
    date_to: date
    rows: list[TrialRow]
    totals: TrialRow | None
    balanced: bool  # суммарные обороты дебета и кредита равны (следствие дебет = кредит)


class LedgerRow(BaseModel):
    entry_id: uuid.UUID
    number: int
    entry_date: date
    description: str
    memo: str | None
    debit: str
    credit: str
    balance_debit: str  # нарастающее сальдо: дебетовое
    balance_credit: str  # или кредитовое


class LedgerOut(BaseModel):
    account_id: uuid.UUID
    code: str
    name: str
    currency: str
    date_from: date
    date_to: date
    opening_debit: str
    opening_credit: str
    rows: list[LedgerRow]
    total: int


# ---------------- правила проводок (срез 5.2) ----------------

Side = Literal["debit", "credit"]
AmountSource = Literal["total_amount", "tax_total", "total_minus_tax"]
RuleKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
DocTypeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


class RuleLineIn(BaseModel):
    account_id: uuid.UUID
    side: Side
    amount_source: AmountSource
    memo: Memo = None


class RuleCreate(BaseModel):
    rule_key: RuleKey
    name: Name
    description: Notes = None
    doc_type: DocTypeName
    effective_from: date | None = None
    effective_to: date | None = None
    lines: Annotated[list[RuleLineIn], Field(min_length=2, max_length=50)]


class RuleUpdate(BaseModel):
    name: Name | None = None
    description: Notes = None
    doc_type: DocTypeName | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    lines: Annotated[list[RuleLineIn], Field(min_length=2, max_length=50)] | None = None


class RuleApprove(BaseModel):
    legal_basis: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class RuleLineOut(BaseModel):
    position: int
    account_id: uuid.UUID
    account_code: str
    account_name: str
    side: str
    amount_source: str
    memo: str | None


class RuleOut(BaseModel):
    id: uuid.UUID
    rule_key: str
    version: int
    name: str
    description: str | None
    doc_type: str
    status: str
    legal_basis: str | None
    effective_from: date | None
    effective_to: date | None
    approved_by_name: str | None
    approved_at: datetime | None
    created_at: datetime
    lines: list[RuleLineOut]


class RuleListItem(BaseModel):
    id: uuid.UUID
    rule_key: str
    version: int
    name: str
    doc_type: str
    status: str
    effective_from: date | None
    effective_to: date | None
    approved_at: datetime | None


class ProposalLine(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    debit: str
    credit: str
    memo: str | None


class ProposalOut(BaseModel):
    rule: RuleRef
    legal_basis: str | None
    entry_date: date | None
    currency: str | None
    description: str
    lines: list[ProposalLine]
    problems: list[str]  # коды причин, по которым провести нельзя
    warnings: list[
        str
    ] = []  # предупреждения, не блокирующие проведение (например, налог не сходится со ставкой)
    posted_entry_id: uuid.UUID | None  # уже проведено по этому правилу (и не сторнировано)


class FromDocumentIn(BaseModel):
    document_id: uuid.UUID
    rule_id: uuid.UUID
    entry_date: date | None = None


# ---------------- курсы валют и налоговые коды (срез 5.3) ----------------

Rate = Annotated[
    Decimal,
    BeforeValidator(_plain),
    Field(gt=0, max_digits=20, decimal_places=6, allow_inf_nan=False),
    AfterValidator(_fits),
]
Percent = Annotated[
    Decimal,
    BeforeValidator(_plain),
    Field(ge=0, le=100, max_digits=7, decimal_places=4, allow_inf_nan=False),
]


class RateIn(BaseModel):
    currency: Currency
    rate_date: date
    nominal: int = Field(default=1, ge=1, le=1_000_000)
    rate: Rate


class RateOut(BaseModel):
    id: uuid.UUID
    currency: str
    rate_date: date
    nominal: int
    rate: str
    source: str
    source_ref: str | None
    created_at: datetime


class CbuImportIn(BaseModel):
    rate_date: date


class CbuImportOut(BaseModel):
    rate_date: date
    imported: list[str]
    skipped_existing: list[str]
    not_enabled: list[str]


class TaxCodeIn(BaseModel):
    code: Code
    name: Name
    rate: Percent
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)] = (
        None
    )
    source_note: Notes = None


class TaxCodeUpdate(BaseModel):
    name: Name | None = None
    rate: Percent | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)] = (
        None
    )
    source_note: Notes = None


class TaxCodeOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    rate: str
    effective_from: date | None
    effective_to: date | None
    source_url: str | None
    source_note: str | None
    status: str
    is_default: bool
    confirmed_by_name: str | None
    confirmed_at: datetime | None


class MissingRate(BaseModel):
    currency: str
    rate_date: date


class BaseTrialBalanceOut(BaseModel):
    currency: str  # базовая валюта организации
    date_from: date
    date_to: date
    rows: list[TrialRow]
    totals: TrialRow | None
    balanced: bool
    complete: bool  # False, если для каких-то проводок не нашлось курса
    missing_rates: list[MissingRate]
    note: str
