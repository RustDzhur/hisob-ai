import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow


class Warehouse(Base):
    """Склад организации. Архивируется, не удаляется (на него будут ссылаться движения)."""

    __tablename__ = "warehouse"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_warehouse_id_org"),
        Index("uq_warehouse_org_name", "organization_id", "name", unique=True),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(CITEXT)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


OP_KINDS = (
    "receipt",
    "issue",
    "transfer",
    "supplier_return",
    "customer_return",
    "count_adjustment",
)


class StockOperation(Base):
    """Складская операция: черновик правится, проведённая неизменяема (исправление — сторно, срез 4.2)."""

    __tablename__ = "stock_operation"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_stock_operation_id_org"),
        ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_operation_warehouse",
        ),
        ForeignKeyConstraint(
            ["to_warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_operation_to_warehouse",
        ),
        Index("ix_stock_operation_org_date", "organization_id", "op_date"),
        CheckConstraint(f"kind IN ({_in(OP_KINDS)})", name="kind"),
        CheckConstraint("status IN ('DRAFT', 'POSTED')", name="status"),
        CheckConstraint("(kind = 'transfer') = (to_warehouse_id IS NOT NULL)", name="to_wh"),
        CheckConstraint(
            "to_warehouse_id IS NULL OR to_warehouse_id <> warehouse_id", name="distinct_wh"
        ),
        CheckConstraint(
            "(status = 'POSTED') = (posted_at IS NOT NULL AND posted_by IS NOT NULL)",
            name="posted",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    op_date: Mapped[date] = mapped_column(Date)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    to_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    posted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class StockOperationLine(Base):
    __tablename__ = "stock_operation_line"
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_line_operation",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_line_product",
        ),
        UniqueConstraint("operation_id", "position", name="uq_stock_line_position"),
        CheckConstraint("quantity > 0", name="positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    position: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))


class StockMovement(Base):
    """Неизменяемая запись об изменении количества (триггер БД запрещает UPDATE/DELETE)."""

    __tablename__ = "stock_movement"
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_movement_operation",
        ),
        ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_movement_warehouse",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_movement_product",
        ),
        Index("ix_stock_movement_product", "organization_id", "product_id", "occurred_at"),
        Index("ix_stock_movement_warehouse", "organization_id", "warehouse_id"),
        Index("ix_stock_movement_operation", "operation_id"),
        CheckConstraint("quantity_delta <> 0", name="nonzero"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    op_date: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class StockBalance(Base):
    """Производный остаток: равен сумме движений; меняется только вместе с ними, под блокировкой строки."""

    __tablename__ = "stock_balance"
    __table_args__ = (
        ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_balance_warehouse",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_balance_product",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), default=Decimal(0))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )
