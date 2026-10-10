import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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
    false,
    func,
    text,
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
        ForeignKeyConstraint(
            ["source_document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_stock_operation_document",
        ),
        ForeignKeyConstraint(
            ["reverses_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_operation_reverses",
        ),
        ForeignKeyConstraint(
            ["reversed_by_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_operation_reversed_by",
        ),
        Index("ix_stock_operation_org_date", "organization_id", "op_date"),
        Index("ix_stock_operation_document", "source_document_id"),
        Index(
            "uq_stock_operation_document_kind",
            "organization_id",
            "source_document_id",
            "kind",
            unique=True,
            postgresql_where=text(
                "source_document_id IS NOT NULL AND reversed_by_id IS NULL AND reverses_id IS NULL"
            ),
        ),
        Index(
            "uq_stock_operation_reverses",
            "reverses_id",
            unique=True,
            postgresql_where=text("reverses_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(reverses_id IS NULL AND reversed_by_id IS NULL) OR status = 'POSTED'",
            name="reversal_posted",
        ),
        CheckConstraint(
            "reverses_id IS NULL OR (reversed_by_id IS NULL AND source_document_id IS NULL)",
            name="reversal_exclusive",
        ),
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
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reversed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
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
        CheckConstraint("quantity <> 0 AND (quantity > 0 OR is_adjustment)", name="quantity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    position: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    # строки корректировки инвентаризации могут быть отрицательными (расхождение со знаком)
    is_adjustment: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


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


class StockCount(Base):
    """Инвентаризация склада: пересчёт, затем проведение расхождений корректирующей операцией."""

    __tablename__ = "stock_count"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_stock_count_id_org"),
        ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_count_warehouse",
        ),
        ForeignKeyConstraint(
            ["operation_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_count_operation",
        ),
        Index(
            "uq_stock_count_active_warehouse",
            "organization_id",
            "warehouse_id",
            unique=True,
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
        CheckConstraint("status IN ('IN_PROGRESS', 'POSTED')", name="status"),
        CheckConstraint(
            "(status = 'POSTED') = (posted_at IS NOT NULL AND posted_by IS NOT NULL)", name="posted"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(
        String(12), default="IN_PROGRESS", server_default="IN_PROGRESS"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    posted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class StockCountLine(Base):
    __tablename__ = "stock_count_line"
    __table_args__ = (
        ForeignKeyConstraint(
            ["count_id", "organization_id"],
            ["stock_count.id", "stock_count.organization_id"],
            name="fk_stock_count_line_count",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_count_line_product",
        ),
        UniqueConstraint("count_id", "product_id", name="uq_stock_count_line_product"),
        CheckConstraint("counted_quantity IS NULL OR counted_quantity >= 0", name="counted"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    count_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    expected_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    counted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    applied_delta: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)


class StockMinLevel(Base):
    __tablename__ = "stock_min_level"
    __table_args__ = (
        ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_min_level_warehouse",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_min_level_product",
        ),
        CheckConstraint("min_quantity > 0", name="positive"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class StockReservation(Base):
    """Резерв: уменьшает доступное количество, физический остаток не меняет."""

    __tablename__ = "stock_reservation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_reservation_warehouse",
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_reservation_product",
        ),
        Index(
            "ix_stock_reservation_active",
            "organization_id",
            "warehouse_id",
            "product_id",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        CheckConstraint("quantity > 0", name="positive"),
        CheckConstraint("status IN ('ACTIVE', 'RELEASED')", name="status"),
        CheckConstraint(
            "(status = 'RELEASED') = (released_at IS NOT NULL AND released_by IS NOT NULL)",
            name="released",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    released_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
