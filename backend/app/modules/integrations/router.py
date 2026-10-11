import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.identity.deps import CurrentPrincipal
from app.modules.integrations import service as svc
from app.modules.integrations.schemas import ProviderOut, SyncIn, SyncRunOut
from app.platform.csrf import require_csrf
from app.platform.db import get_db

router = APIRouter(prefix="/organizations/{org_id}/integrations", tags=["integrations"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("", response_model=list[ProviderOut])
def providers(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> list[ProviderOut]:
    return svc.list_providers(db, principal, org_id)


@router.get("/runs", response_model=list[SyncRunOut])
def runs(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    provider: Annotated[str | None, Query(max_length=30)] = None,
) -> list[SyncRunOut]:
    return svc.list_runs(db, principal, org_id, provider)


@router.post("/{provider}/enable", response_model=ProviderOut)
def enable(
    org_id: uuid.UUID, provider: str, principal: CurrentPrincipal, db: DbSession, _: Csrf
) -> ProviderOut:
    return svc.set_enabled(db, principal, org_id, provider, True)


@router.post("/{provider}/disable", response_model=ProviderOut)
def disable(
    org_id: uuid.UUID, provider: str, principal: CurrentPrincipal, db: DbSession, _: Csrf
) -> ProviderOut:
    return svc.set_enabled(db, principal, org_id, provider, False)


@router.post("/{provider}/sync", response_model=SyncRunOut)
def sync(
    org_id: uuid.UUID,
    provider: str,
    body: SyncIn,
    principal: CurrentPrincipal,
    db: DbSession,
    _: Csrf,
) -> SyncRunOut:
    return svc.sync(db, principal, org_id, provider, body.request_id)
