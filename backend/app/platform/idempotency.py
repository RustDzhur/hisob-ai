import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, Uuid, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.platform.base import Base, utcnow
from app.platform.errors import AppError


class IdempotencyKey(Base):
    """Результат уже выполненной операции для пары (пользователь, ключ)."""

    __tablename__ = "idempotency_key"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


def validate_key(key: str | None) -> str:
    if key is None or not (8 <= len(key) <= 200) or not key.isprintable():
        raise AppError(
            400,
            "idempotency_key_required",
            "Нужен заголовок Idempotency-Key (8–200 печатных символов).",
        )
    return key


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def find(db: Session, user_id: uuid.UUID, key: str, req_hash: str) -> IdempotencyKey | None:
    """Сохранённый результат; тот же ключ с другим телом запроса — ошибка."""
    row = db.scalar(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
    )
    if row is not None and row.request_hash != req_hash:
        raise AppError(
            422,
            "idempotency_key_reuse",
            "Этот Idempotency-Key уже использован с другими данными.",
        )
    return row


def store(
    db: Session,
    user_id: uuid.UUID,
    key: str,
    req_hash: str,
    status: int,
    body: dict[str, Any],
) -> None:
    """Вставка в транзакции операции. Гонка двух одинаковых запросов -> IntegrityError на flush."""
    db.add(
        IdempotencyKey(
            user_id=user_id,
            key=key,
            request_hash=req_hash,
            response_status=status,
            response_body=body,
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409, "idempotency_conflict", "Такой запрос уже выполняется. Повторите через секунду."
        ) from exc
