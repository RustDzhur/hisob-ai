import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.catalog import counterparties as svc
from app.modules.catalog.schemas import CounterpartyCreate, CounterpartyOut, CounterpartyUpdate
from app.modules.identity.deps import CurrentPrincipal
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/counterparties", tags=["counterparties"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("", response_model=Page[CounterpartyOut])
def list_counterparties(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    role: Annotated[Literal["all", "customer", "supplier"], Query()] = "all",
    status: Annotated[Literal["active", "archived", "all"], Query()] = "active",
) -> Page[CounterpartyOut]:
    return svc.list_counterparties(db, principal, org_id, lq, role=role, status=status)


@router.post("", response_model=CounterpartyOut, status_code=201)
def create_counterparty(
    org_id: uuid.UUID,
    body: CounterpartyCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> CounterpartyOut:
    return svc.create_counterparty(db, principal, org_id, body)


@router.get("/{counterparty_id}", response_model=CounterpartyOut)
def get_counterparty(
    org_id: uuid.UUID, counterparty_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> CounterpartyOut:
    return svc.get_counterparty(db, principal, org_id, counterparty_id)


@router.patch("/{counterparty_id}", response_model=CounterpartyOut)
def update_counterparty(
    org_id: uuid.UUID,
    counterparty_id: uuid.UUID,
    body: CounterpartyUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> CounterpartyOut:
    return svc.update_counterparty(db, principal, org_id, counterparty_id, body)


@router.post("/{counterparty_id}/archive", response_model=CounterpartyOut)
def archive_counterparty(
    org_id: uuid.UUID,
    counterparty_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> CounterpartyOut:
    return svc.set_archived(db, principal, org_id, counterparty_id, True)


@router.post("/{counterparty_id}/restore", response_model=CounterpartyOut)
def restore_counterparty(
    org_id: uuid.UUID,
    counterparty_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> CounterpartyOut:
    return svc.set_archived(db, principal, org_id, counterparty_id, False)
