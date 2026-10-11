"""Выполнение вопроса в воркере: ключи AI есть только здесь. Идемпотентно по статусу сообщения."""

import logging
import uuid

from sqlalchemy import select

from app.modules.audit import service as audit
from app.modules.copilot import engine
from app.modules.copilot.models import CopilotMessage
from app.modules.copilot.service import prior_messages
from app.modules.documents.extraction.providers import ProviderError
from app.modules.identity.models import User
from app.modules.identity.service import Principal
from app.platform.base import utcnow
from app.platform.db import get_sessionmaker, set_context

log = logging.getLogger("app.copilot")


def run_answer(message_id: uuid.UUID, org_id: uuid.UUID) -> None:
    with get_sessionmaker()() as db:
        set_context(db, org_id=org_id)
        msg = db.scalar(
            select(CopilotMessage).where(CopilotMessage.id == message_id).with_for_update()
        )
        if msg is None or msg.status != "PENDING":
            return
        user = db.get(User, msg.user_id)
        question = db.scalar(
            select(CopilotMessage.content)
            .where(
                CopilotMessage.conversation_id == msg.conversation_id,
                CopilotMessage.organization_id == org_id,
                CopilotMessage.role == "user",
                CopilotMessage.created_at < msg.created_at,
            )
            .order_by(CopilotMessage.created_at.desc())
            .limit(1)
        )
        if user is None or question is None:
            msg.status, msg.error_code, msg.completed_at = "FAILED", "context_missing", utcnow()
            db.commit()
            return
        msg.status = "RUNNING"
        locale, conv_id, created = msg.locale, msg.conversation_id, msg.created_at
        db.commit()
        set_context(db, user_id=user.id, org_id=org_id)
        principal = Principal(user=user, session_id=uuid.UUID(int=0))
        prior = prior_messages(db, org_id, conv_id, created)
        # вопрос уже в prior (он DONE и раньше ответа): убираем, engine добавит его сам
        if prior and prior[-1]["role"] == "user" and prior[-1]["text"] == question:
            prior = prior[:-1]
        try:
            res = engine.run(db, principal, org_id, question, prior, locale)
            msg = db.scalar(
                select(CopilotMessage).where(CopilotMessage.id == message_id).with_for_update()
            )
            assert msg is not None
            msg.content, msg.status = res.text, "DONE"
            msg.tool_trace = res.trace
            msg.provider, msg.model = res.provider, res.model
            msg.tokens_in, msg.tokens_out = res.tokens_in, res.tokens_out
            msg.completed_at = utcnow()
            audit.record(
                db,
                action="copilot.answered",
                actor_user_id=user.id,
                organization_id=org_id,
                object_type="copilot_message",
                object_id=msg.id,
                after={
                    "tools": [t.get("tool") for t in res.trace],
                    "provider": res.provider,
                    "tokens_in": res.tokens_in,
                    "tokens_out": res.tokens_out,
                },
            )
            db.commit()
        except ProviderError as exc:
            db.rollback()
            _fail(db, org_id, message_id, exc.code)
        except Exception:
            log.exception("copilot answer failed message=%s", message_id)
            db.rollback()
            _fail(db, org_id, message_id, "internal_error")


def _fail(db, org_id: uuid.UUID, message_id: uuid.UUID, code: str) -> None:  # type: ignore[no-untyped-def]
    set_context(db, org_id=org_id)
    msg = db.scalar(select(CopilotMessage).where(CopilotMessage.id == message_id).with_for_update())
    if msg is not None and msg.status in ("PENDING", "RUNNING"):
        msg.status, msg.error_code, msg.completed_at = "FAILED", code, utcnow()
        db.commit()
