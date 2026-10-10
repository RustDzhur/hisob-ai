import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow


class UnitOfMeasure(Base):
    """Единица измерения. organization_id IS NULL — системная (видна всем, не меняется).

    Официальные коды классификаторов не заполняются без утверждённого источника (official_code).
    """

    __tablename__ = "unit_of_measure"
    __table_args__ = (
        Index("uq_unit_org_code", "organization_id", "code", unique=True),
        Index(
            "uq_unit_system_code",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        CheckConstraint(
            "name_uz IS NOT NULL OR name_ru IS NOT NULL OR name_en IS NOT NULL", name="has_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(CITEXT)
    name_uz: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class Counterparty(Base):
    """Контрагент организации. Архивируется, не удаляется (на него могут ссылаться документы)."""

    __tablename__ = "counterparty"
    __table_args__ = (
        Index(
            "uq_counterparty_org_tin",
            "organization_id",
            "tin",
            unique=True,
            postgresql_where=text("tin IS NOT NULL"),
        ),
        Index("ix_counterparty_org_name", "organization_id", "name"),
        UniqueConstraint("id", "organization_id", name="uq_counterparty_id_org"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(Text)
    tin: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class Product(Base):
    """Товар или услуга. Цена — NUMERIC с обязательной валютой; без курсов и пересчёта."""

    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_product_id_org"),
        Index("uq_product_org_sku", "organization_id", "sku", unique=True),
        Index("ix_product_org_name", "organization_id", "name"),
        CheckConstraint("kind IN ('goods','service')", name="kind"),
        CheckConstraint("(price IS NULL) = (price_currency IS NULL)", name="price_with_currency"),
        CheckConstraint("price IS NULL OR price >= 0", name="price_non_negative"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    kind: Mapped[str] = mapped_column(Text)
    sku: Mapped[str] = mapped_column(CITEXT)
    name: Mapped[str] = mapped_column(Text)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("unit_of_measure.id"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    price_currency: Mapped[str | None] = mapped_column(ForeignKey("currency.code"), nullable=True)
    ikpu: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ProductIdentifier(Base):
    """Дополнительные идентификаторы товара (сейчас только штрихкоды); уникальны в организации."""

    __tablename__ = "product_identifier"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_product_identifier_product",
        ),
        UniqueConstraint("organization_id", "kind", "value", name="uq_product_identifier_value"),
        CheckConstraint("kind IN ('barcode')", name="kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(20), default="barcode")
    value: Mapped[str] = mapped_column(CITEXT)
