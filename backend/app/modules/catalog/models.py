import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
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
