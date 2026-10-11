"""Use cases Copilot: вопросы ставятся в очередь воркера; диалоги личные для пользователя."""

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.copilot.models import CopilotConversation, CopilotMessage
from app.modules.copilot.schemas import (
    AskIn,
    AskOut,
    ConversationDetail,
    ConversationOut,
    CopilotStatus,
    MessageOut,
)
from app.modules.documents.models import AIRuntime
from app.modules.identity.service import Principal
from app.modules.tenancy.access import authorize
from app.platform.base import utcnow
from app.platform.errors import AppError

log = logging.getLogger("app.copilot")

PERM = "copilot.use"
RATE_LIMIT = 20  # вопросов за окно на пользователя
RATE_WINDOW = timedelta(minutes=10)
HISTORY_MESSAGES = 10


def _fail(http: int, code: str, message: str) -> AppError:
    return AppError(http, code, message)


def _out(m: CopilotMessage, mock: bool = False) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        status=m.status,
        tool_trace=m.tool_trace or [],
        provider=m.provider,
        model=m.model,
        mock=m.provider == "mock",
        error_code=m.error_code,
        created_at=m.created_at,
        completed_at=m.completed_at,
    )


def status(db: Session, principal: Principal, org_id: uuid.UUID) -> CopilotStatus:
    authorize(db, principal, org_id, PERM)
    rt = db.get(AIRuntime, 1)
    if rt is None:
        return CopilotStatus(configured=False, provider=None, model=None, mock=False)
    return CopilotStatus(
        configured=rt.configured,
        provider=rt.provider,
        model=rt.model or None,
        mock=rt.provider == "mock",
    )


def _conversation(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID
) -> CopilotConversation:
    c = db.scalar(
        select(CopilotConversation).where(
            CopilotConversation.id == cid,
            CopilotConversation.organization_id == org_id,
            CopilotConversation.user_id
            == principal.user.id,  # чужие диалоги неотличимы от несуществующих
        )
    )
    if c is None:
        raise _fail(404, "not_found", "Диалог не найден.")
    return c


def ask(db: Session, principal: Principal, org_id: uuid.UUID, data: AskIn) -> AskOut:
    authorize(db, principal, org_id, PERM)
    since = utcnow() - RATE_WINDOW
    recent = db.scalar(
        select(func.count())
        .select_from(CopilotMessage)
        .where(
            CopilotMessage.organization_id == org_id,
            CopilotMessage.user_id == principal.user.id,
            CopilotMessage.role == "user",
            CopilotMessage.created_at >= since,
        )
    )
    if (recent or 0) >= RATE_LIMIT:
        raise _fail(
            429, "copilot_rate_limited", "Слишком много вопросов. Подождите несколько минут."
        )
    if data.conversation_id is not None:
        conv = _conversation(db, principal, org_id, data.conversation_id)
        busy = db.scalar(
            select(func.count())
            .select_from(CopilotMessage)
            .where(
                CopilotMessage.conversation_id == conv.id,
                CopilotMessage.organization_id == org_id,
                CopilotMessage.status.in_(("PENDING", "RUNNING")),
                CopilotMessage.created_at >= utcnow() - timedelta(minutes=10),
            )
        )
        if busy:
            raise _fail(409, "copilot_busy", "Дождитесь ответа на предыдущий вопрос.")
        conv.updated_at = utcnow()
    else:
        conv = CopilotConversation(
            organization_id=org_id, user_id=principal.user.id, title=data.question[:60]
        )
        db.add(conv)
        db.flush()
    now = utcnow()
    db.add(
        CopilotMessage(
            organization_id=org_id,
            conversation_id=conv.id,
            user_id=principal.user.id,
            role="user",
            content=data.question,
            status="DONE",
            locale=data.locale,
            created_at=now,
            completed_at=now,
        )
    )
    answer = CopilotMessage(
        organization_id=org_id,
        conversation_id=conv.id,
        user_id=principal.user.id,
        role="assistant",
        content="",
        status="PENDING",
        locale=data.locale,
        created_at=now + timedelta(milliseconds=1),
    )
    db.add(answer)
    audit.record(
        db,
        action="copilot.asked",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="copilot_conversation",
        object_id=conv.id,
        after={"question_length": len(data.question)},  # текст вопроса в аудит не пишем
    )
    db.commit()

    from app.worker import copilot_answer  # отложенный импорт: воркер тянет модули приложения

    try:
        copilot_answer.send(str(answer.id), str(org_id))
    except Exception as exc:
        log.warning("copilot enqueue failed type=%s", type(exc).__name__)
        answer.status, answer.error_code, answer.completed_at = (
            "FAILED",
            "queue_unavailable",
            utcnow(),
        )
        db.commit()
        raise _fail(503, "queue_unavailable", "Очередь недоступна. Повторите позже.") from exc
    db.refresh(answer)
    return AskOut(conversation_id=conv.id, message=_out(answer))


def list_conversations(
    db: Session, principal: Principal, org_id: uuid.UUID
) -> list[ConversationOut]:
    authorize(db, principal, org_id, PERM)
    rows = db.scalars(
        select(CopilotConversation)
        .where(
            CopilotConversation.organization_id == org_id,
            CopilotConversation.user_id == principal.user.id,
        )
        .order_by(CopilotConversation.updated_at.desc(), CopilotConversation.id)
        .limit(30)
    )
    return [ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at) for c in rows]


def get_conversation(
    db: Session, principal: Principal, org_id: uuid.UUID, cid: uuid.UUID
) -> ConversationDetail:
    authorize(db, principal, org_id, PERM)
    c = _conversation(db, principal, org_id, cid)
    msgs = db.scalars(
        select(CopilotMessage)
        .where(CopilotMessage.conversation_id == c.id, CopilotMessage.organization_id == org_id)
        .order_by(CopilotMessage.created_at, CopilotMessage.id)
    )
    return ConversationDetail(
        id=c.id, title=c.title, updated_at=c.updated_at, messages=[_out(m) for m in msgs]
    )


def prior_messages(
    db: Session, org_id: uuid.UUID, conversation_id: uuid.UUID, before: Any
) -> list[dict[str, str]]:
    """Предыдущие реплики диалога (только текст, без результатов инструментов)."""
    rows = db.scalars(
        select(CopilotMessage)
        .where(
            CopilotMessage.organization_id == org_id,
            CopilotMessage.conversation_id == conversation_id,
            CopilotMessage.status == "DONE",
            CopilotMessage.created_at < before,
            CopilotMessage.content != "",
        )
        .order_by(CopilotMessage.created_at.desc(), CopilotMessage.id.desc())
        .limit(HISTORY_MESSAGES)
    )
    return [{"role": m.role, "text": m.content} for m in reversed(list(rows))]
