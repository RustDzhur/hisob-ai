import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow


class BankAccount(Base):
    """Банковский счёт организации; профиль импорта (сопоставление колонок выписки) — JSON, задаёт пользователь."""

    __tablename__ = "bank_account"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_bank_account_id_org"),
        UniqueConstraint("organization_id", "account_number", name="uq_bank_account_number"),
        CheckConstraint(
            "length(btrim(name)) > 0 AND length(btrim(account_number)) > 0", name="not_blank"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(Text)
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_number: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(ForeignKey("currency.code"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class BankImport(Base):
    __tablename__ = "bank_import"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bank_account_id", "organization_id"],
            ["bank_account.id", "bank_account.organization_id"],
            name="fk_bank_import_account",
        ),
        UniqueConstraint("id", "organization_id", name="uq_bank_import_id_org"),
        UniqueConstraint(
            "organization_id", "bank_account_id", "sha256", name="uq_bank_import_file"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(Text)
    rows_total: Mapped[int] = mapped_column(Integer)
    rows_imported: Mapped[int] = mapped_column(Integer)
    rows_duplicate: Mapped[int] = mapped_column(Integer)
    rows_error: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class BankTransaction(Base):
    """Операция выписки: факты неизменяемы (триггер БД), меняется только статус сверки и привязка контрагента."""

    __tablename__ = "bank_transaction"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bank_account_id", "organization_id"],
            ["bank_account.id", "bank_account.organization_id"],
            name="fk_bank_txn_account",
        ),
        ForeignKeyConstraint(
            ["import_id", "organization_id"],
            ["bank_import.id", "bank_import.organization_id"],
            name="fk_bank_txn_import",
        ),
        ForeignKeyConstraint(
            ["counterparty_id", "organization_id"],
            ["counterparty.id", "counterparty.organization_id"],
            name="fk_bank_txn_counterparty",
        ),
        UniqueConstraint("id", "organization_id", name="uq_bank_txn_id_org"),
        UniqueConstraint(
            "organization_id", "bank_account_id", "row_hash", name="uq_bank_txn_row_hash"
        ),
        Index("ix_bank_txn_account_date", "organization_id", "bank_account_id", "txn_date"),
        Index("ix_bank_txn_status", "organization_id", "status"),
        CheckConstraint("amount <> 0", name="nonzero"),
        CheckConstraint("status IN ('UNMATCHED', 'MATCHED', 'IGNORED')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    import_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    row_number: Mapped[int] = mapped_column(Integer)
    txn_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(ForeignKey("currency.code"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_tin: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), default="UNMATCHED", server_default="UNMATCHED")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class BankMatch(Base):
    """Сопоставление части банковской операции с документом (разбиение и объединение — несколько строк)."""

    __tablename__ = "bank_match"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "organization_id"],
            ["bank_transaction.id", "bank_transaction.organization_id"],
            name="fk_bank_match_txn",
        ),
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_bank_match_document",
        ),
        Index("ix_bank_match_txn", "organization_id", "transaction_id"),
        Index("ix_bank_match_document", "organization_id", "document_id"),
        CheckConstraint("amount > 0", name="positive"),
        CheckConstraint("status IN ('CONFIRMED', 'CANCELLED')", name="status"),
        CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="cancelled",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    status: Mapped[str] = mapped_column(String(10), default="CONFIRMED", server_default="CONFIRMED")
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BankMatchRejection(Base):
    """Предложенное сопоставление, которое пользователь отклонил: больше не предлагается."""

    __tablename__ = "bank_match_rejection"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "organization_id"],
            ["bank_transaction.id", "bank_transaction.organization_id"],
            name="fk_bank_rejection_txn",
        ),
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_bank_rejection_document",
        ),
        UniqueConstraint(
            "organization_id", "transaction_id", "document_id", name="uq_bank_rejection"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    rejected_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
