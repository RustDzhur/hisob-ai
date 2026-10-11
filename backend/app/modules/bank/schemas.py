import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, StringConstraints

from app.platform.validators import reject_non_plain_number


def _blank_to_none(v: str | None) -> str | None:
    return v if v else None


def _account_number(v: str) -> str:
    v = re.sub(r"\s+", "", v).upper()
    if not re.fullmatch(r"[A-Z0-9]{4,34}", v):
        raise ValueError("account_number_invalid")
    return v


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Opt = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=500),
    AfterValidator(_blank_to_none),
]
AccountNumber = Annotated[str, AfterValidator(_account_number)]
Currency = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=3, max_length=3)
]


class BankAccountCreate(BaseModel):
    name: Name
    bank_name: Opt = None
    account_number: AccountNumber
    currency: Currency
    notes: Opt = None


class BankAccountUpdate(BaseModel):
    name: Name | None = None
    bank_name: Opt = None
    notes: Opt = None


class BankAccountOut(BaseModel):
    id: uuid.UUID
    name: str
    bank_name: str | None
    account_number: str
    currency: str
    notes: str | None
    has_profile: bool
    archived: bool
    transactions_count: int
    created_at: datetime


class HeadersOut(BaseModel):
    headers: list[str]
    sample: list[dict[str, str]]


class PreviewRow(BaseModel):
    row_number: int
    txn_date: date
    amount: str
    description: str | None
    reference: str | None
    counterparty_name: str | None
    counterparty_tin: str | None
    duplicate: bool


class RowErrorOut(BaseModel):
    row_number: int
    code: str


class PreviewOut(BaseModel):
    total: int
    valid: int
    errors_count: int
    duplicates: int
    would_import: int
    errors: list[RowErrorOut]
    sample: list[PreviewRow]
    already_imported: bool  # тот же файл уже загружен по этому счёту


class ImportOut(BaseModel):
    id: uuid.UUID
    bank_account_id: uuid.UUID
    filename: str
    rows_total: int
    rows_imported: int
    rows_duplicate: int
    rows_error: int
    created_by_name: str | None
    created_at: datetime


class NameRef(BaseModel):
    id: uuid.UUID
    name: str


class TransactionOut(BaseModel):
    id: uuid.UUID
    bank_account: NameRef
    import_id: uuid.UUID
    txn_date: date
    amount: str
    currency: str
    description: str | None
    reference: str | None
    counterparty_name: str | None
    counterparty_tin: str | None
    counterparty: NameRef | None
    status: str
    matched_amount: str = "0"
    remaining: str = "0"


# ---------------- сверка (срез 6.2) ----------------


def _plain(v: object) -> object:
    return reject_non_plain_number(v, "number_invalid")


MatchAmount = Annotated[
    Decimal,
    BeforeValidator(_plain),
    Field(gt=0, max_digits=20, decimal_places=4, allow_inf_nan=False),
]
Note = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=500),
    AfterValidator(_blank_to_none),
]


class AllocationIn(BaseModel):
    document_id: uuid.UUID
    amount: MatchAmount


class MatchCreate(BaseModel):
    """Разбиение: одна операция -> несколько документов."""

    allocations: Annotated[list[AllocationIn], Field(min_length=1, max_length=50)]
    note: Note = None


class MergeAllocationIn(BaseModel):
    transaction_id: uuid.UUID
    amount: MatchAmount


class MergeCreate(BaseModel):
    """Объединение: несколько операций -> один документ."""

    document_id: uuid.UUID
    allocations: Annotated[list[MergeAllocationIn], Field(min_length=1, max_length=50)]
    note: Note = None


class RejectCandidateIn(BaseModel):
    document_id: uuid.UUID


class IgnoreIn(BaseModel):
    reason: Note = None


class Reason(BaseModel):
    code: str
    params: dict[str, str] = {}


class DocBrief(BaseModel):
    id: uuid.UUID
    number: str | None
    doc_type: str
    doc_date: date | None
    counterparty_name: str | None
    total: str
    remaining: str  # не покрыто сопоставлениями


class TxnBrief(BaseModel):
    id: uuid.UUID
    txn_date: date
    amount: str
    currency: str
    description: str | None
    counterparty_name: str | None
    remaining: str  # не сопоставлено (по модулю)
    bank_account_name: str


class DocCandidate(BaseModel):
    document: DocBrief
    score: int
    reasons: list[Reason]
    suggested_amount: str


class TxnCandidate(BaseModel):
    transaction: TxnBrief
    score: int
    reasons: list[Reason]
    suggested_amount: str


class MatchOut(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    document_id: uuid.UUID
    document_number: str | None
    document_type: str
    txn_date: date
    amount: str
    currency: str
    status: str
    note: str | None
    reasons: list[Reason]
    created_by_name: str | None
    created_at: datetime
    cancelled_at: datetime | None


class TransactionDetailOut(TransactionOut):
    matched_amount: str
    remaining: str
    matches: list[MatchOut]
    candidates: list[DocCandidate]


class DocumentBankOut(BaseModel):
    document_id: uuid.UUID
    currency: str | None
    total: str | None
    matched_total: str
    remaining: str | None
    matches: list[MatchOut]
    candidates: list[TxnCandidate]
