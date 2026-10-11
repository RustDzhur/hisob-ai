import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.modules.copilot import service as svc
from app.modules.copilot.schemas import (
    AskIn,
    AskOut,
    ConversationDetail,
    ConversationOut,
    CopilotStatus,
)
from app.modules.identity.deps import CurrentPrincipal
from app.platform.csrf import require_csrf
from app.platform.db import get_db

router = APIRouter(prefix="/organizations/{org_id}/copilot", tags=["copilot"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("/status", response_model=CopilotStatus)
def status(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> CopilotStatus:
    return svc.status(db, principal, org_id)


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(
    org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> list[ConversationOut]:
    return svc.list_conversations(db, principal, org_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(
    org_id: uuid.UUID, conversation_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> ConversationDetail:
    return svc.get_conversation(db, principal, org_id, conversation_id)


@router.post("/ask", response_model=AskOut, status_code=202)
def ask(
    org_id: uuid.UUID, body: AskIn, principal: CurrentPrincipal, db: DbSession, _: Csrf
) -> AskOut:
    return svc.ask(db, principal, org_id, body)
