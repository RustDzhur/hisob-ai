import uuid
from datetime import datetime

from pydantic import BaseModel


class ProviderOut(BaseModel):
    code: str
    title: str
    kind: str
    status: str  # AVAILABLE | MOCK | NOT_CONFIGURED
    docs_received: bool
    capabilities: list[str]
    enabled: bool = False
    can_sync: bool = False
    last_sync_at: datetime | None = None
    sync_cursor: str | None = None


class SyncIn(BaseModel):
    request_id: uuid.UUID


class SyncRunOut(BaseModel):
    id: uuid.UUID
    provider: str
    mode: str
    request_id: uuid.UUID
    status: str
    cursor_before: str | None
    cursor_after: str | None
    records_count: int
    error_code: str | None
    created_by_name: str | None
    created_at: datetime
