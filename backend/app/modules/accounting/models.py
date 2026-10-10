import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow


class Account(Base):
    """Счёт плана счетов. Предзаполненных счетов нет: план вводит бухгалтер (ТЗ §10)."""

    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_account_id_org"),
        UniqueConstraint("organization_id", "code", name="uq_account_org_code"),
        ForeignKeyConstraint(
            ["parent_id", "organization_id"],
            ["account.id", "account.organization_id"],
            name="fk_account_parent",
        ),
        CheckConstraint("length(btrim(code)) > 0 AND length(btrim(name)) > 0", name="not_blank"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    allow_posting: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class AccountingPeriod(Base):
    __tablename__ = "accounting_period"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_period_id_org"),
        UniqueConstraint("organization_id", "start_date", name="uq_period_org_start"),
        CheckConstraint("end_date >= start_date", name="dates"),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="status"),
        CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL AND closed_by IS NOT NULL)", name="closed"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", server_default="OPEN")
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class AccountingCounter(Base):
    """Сквозная нумерация проводок организации (атомарное увеличение)."""

    __tablename__ = "accounting_counter"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id"), primary_key=True
    )
    last_number: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class JournalEntry(Base):
    """Проводка. Неизменяема (триггер БД); исправление — сторно новой записью."""

    __tablename__ = "journal_entry"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_journal_entry_id_org"),
        UniqueConstraint("organization_id", "number", name="uq_journal_entry_number"),
        UniqueConstraint("reverses_id", name="uq_journal_entry_reverses"),
        ForeignKeyConstraint(
            ["period_id", "organization_id"],
            ["accounting_period.id", "accounting_period.organization_id"],
            name="fk_journal_entry_period",
        ),
        ForeignKeyConstraint(
            ["reverses_id", "organization_id"],
            ["journal_entry.id", "journal_entry.organization_id"],
            name="fk_journal_entry_reverses",
        ),
        Index("ix_journal_entry_org_date", "organization_id", "entry_date", "number"),
        CheckConstraint("length(btrim(description)) > 0", name="description"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    number: Mapped[int] = mapped_column(BigInteger)
    entry_date: Mapped[date] = mapped_column(Date)
    period_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    currency: Mapped[str] = mapped_column(ForeignKey("currency.code"))
    description: Mapped[str] = mapped_column(Text)
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class JournalLine(Base):
    __tablename__ = "journal_line"
    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "organization_id"],
            ["journal_entry.id", "journal_entry.organization_id"],
            name="fk_journal_line_entry",
        ),
        ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["account.id", "account.organization_id"],
            name="fk_journal_line_account",
        ),
        UniqueConstraint("entry_id", "position", name="uq_journal_line_position"),
        Index("ix_journal_line_account", "organization_id", "account_id"),
        CheckConstraint(
            "debit >= 0 AND credit >= 0 AND ((debit > 0) <> (credit > 0))", name="one_side"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    position: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    debit: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal(0))
    credit: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal(0))
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
