import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id, utcnow


class CopilotConversation(Base):
    __tablename__ = "copilot_conversation"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_copilot_conv_id_org"),
        Index("ix_copilot_conv_user", "organization_id", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class CopilotMessage(Base):
    __tablename__ = "copilot_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["copilot_conversation.id", "copilot_conversation.organization_id"],
            name="fk_copilot_msg_conv",
        ),
        Index("ix_copilot_msg_conv", "organization_id", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(10))
    locale: Mapped[str] = mapped_column(String(5), default="ru", server_default="ru")
    tool_trace: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
