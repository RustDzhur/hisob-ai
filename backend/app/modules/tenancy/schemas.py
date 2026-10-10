import re
import uuid
import zoneinfo
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

DEFAULT_TIMEZONE = "Asia/Tashkent"
DEFAULT_CURRENCY = "UZS"  # Q5: базовая валюта подтверждается владельцем

_TIN_RE = re.compile(r"^\d{9,14}$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")


def _tin(v: str | None) -> str | None:
    if v is None or v.strip() == "":
        return None
    v = v.strip()
    # Предварительная проверка формата (только цифры). Точные правила ИНН/СТИР
    # подтверждает бухгалтер (Q9) — до этого длина не фиксируется жёстко.
    if not _TIN_RE.match(v):
        raise ValueError("ИНН/СТИР: только цифры, 9–14 знаков.")
    return v


def _tz(v: str) -> str:
    if v not in zoneinfo.available_timezones():
        raise ValueError("Неизвестный часовой пояс.")
    return v


def _ccy(v: str) -> str:
    v = v.strip().upper()
    if not _CCY_RE.match(v):
        raise ValueError("Код валюты: 3 латинские буквы (например, UZS).")
    return v


class OrganizationCreate(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=200)]
    tin: Annotated[str | None, AfterValidator(_tin)] = None
    timezone: Annotated[str, AfterValidator(_tz)] = DEFAULT_TIMEZONE
    base_currency: Annotated[str, AfterValidator(_ccy)] = DEFAULT_CURRENCY


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tin: str | None
    timezone: str
    base_currency: str
    role: str
    permissions: list[str]


# ---------- участники и приглашения ----------


class MemberOut(BaseModel):
    id: uuid.UUID  # id членства
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str
    joined_at: datetime


class MemberUpdate(BaseModel):
    role_code: str | None = None
    status: Literal["active", "suspended"] | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role_code: str = Field(min_length=1, max_length=50)


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    expired: bool
    created_at: datetime


class InvitationCreated(InvitationOut):
    # Токен показывается один раз: в БД хранится только его хэш.
    token: str


class InvitationPreview(BaseModel):
    organization_name: str
    role: str
    email: str
    state: Literal["pending", "expired", "accepted", "revoked"]


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=200)


# ---------- журнал аудита ----------


class AuditActor(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    action: str
    actor: AuditActor | None
    object_type: str | None
    object_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    correlation_id: str | None
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int
    page: int
    page_size: int
