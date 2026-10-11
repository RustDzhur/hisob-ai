import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints


class AskIn(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    conversation_id: uuid.UUID | None = None
    locale: Literal["ru", "uz", "en"] = "ru"


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    status: str
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    mock: bool = False
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AskOut(BaseModel):
    conversation_id: uuid.UUID
    message: MessageOut  # ответ в статусе PENDING; результат — опросом разговора


class CopilotStatus(BaseModel):
    configured: bool
    provider: str | None
    model: str | None
    mock: bool
