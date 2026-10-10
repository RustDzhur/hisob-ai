import uuid
from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow

# Типы документов (ТЗ §8) и полный набор статусов (ТЗ §8). В срезе 3.1 используются
# RECEIVED→NEEDS_REVIEW→APPROVED/REJECTED/DUPLICATE/ARCHIVED; остальные — для следующих срезов.
DOC_TYPES = (
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
)
STATUSES = (
    "RECEIVED",
    "VALIDATING",
    "QUEUED",
    "EXTRACTING",
    "NORMALIZING",
    "MATCHING",
    "NEEDS_REVIEW",
    "APPROVED",
    "POSTED",
    "RECONCILED",
    "ARCHIVED",
    "DUPLICATE",
    "REJECTED",
    "FAILED",
    "CANCELLED",
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_document_id_org"),
        ForeignKeyConstraint(
            ["counterparty_id", "organization_id"],
            ["counterparty.id", "counterparty.organization_id"],
            name="fk_document_counterparty",
        ),
        Index("ix_document_org_created", "organization_id", "created_at"),
        Index("ix_document_org_status", "organization_id", "status"),
        Index("ix_document_org_cp_number", "organization_id", "counterparty_id", "number"),
        CheckConstraint(f"doc_type IN ({_in(DOC_TYPES)})", name="doc_type"),
        CheckConstraint(f"status IN ({_in(STATUSES)})", name="status"),
        CheckConstraint("total_amount IS NULL OR total_amount >= 0", name="total_non_negative"),
        CheckConstraint("tax_total IS NULL OR tax_total >= 0", name="tax_non_negative"),
        CheckConstraint(
            "(status = 'REJECTED') = (rejection_reason IS NOT NULL)", name="reject_reason"
        ),
        CheckConstraint(
            "(status = 'DUPLICATE') = (duplicate_of_id IS NOT NULL)", name="duplicate_of"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    doc_type: Mapped[str] = mapped_column(String(20), default="other")
    status: Mapped[str] = mapped_column(String(20), default="NEEDS_REVIEW")
    number: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    currency: Mapped[str | None] = mapped_column(ForeignKey("currency.code"), nullable=True)
    # Суммы — как напечатано в документе; налоги не вычисляются (правила не утверждены).
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    tax_total: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class DocumentFile(Base):
    __tablename__ = "document_file"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_document_file_document",
        ),
        UniqueConstraint("organization_id", "sha256", name="uq_document_file_sha256"),
        UniqueConstraint("document_id", name="uq_document_file_document"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(
        Text
    )  # только для показа; в ключ хранилища не попадает
    storage_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class DocumentLine(Base):
    __tablename__ = "document_line"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_document_line_document",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_document_line_product",
        ),
        UniqueConstraint("document_id", "position", name="uq_document_line_position"),
        CheckConstraint(
            "(quantity IS NULL OR quantity >= 0) AND (unit_price IS NULL OR unit_price >= 0) AND "
            "(discount IS NULL OR discount >= 0) AND (tax_amount IS NULL OR tax_amount >= 0) AND "
            "(amount IS NULL OR amount >= 0)",
            name="non_negative",
        ),
        CheckConstraint("length(btrim(description)) > 0", name="description_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("unit_of_measure.id"), nullable=True
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    discount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
