import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow


class AuditEvent(Base):
    """Append-only: UPDATE/DELETE запрещены триггером и правами роли приложения."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_org_time", "organization_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(Text)
    object_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
