import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.accounting import service as svc
from app.modules.accounting.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    EntryCreate,
    EntryListItem,
    EntryOut,
    EntryReverse,
    LedgerOut,
    PeriodCreate,
    PeriodOut,
    TrialBalanceOut,
)
from app.modules.identity.deps import CurrentPrincipal
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/accounting", tags=["accounting"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("/accounts", response_model=Page[AccountOut])
def list_accounts(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[Literal["active", "archived", "all"], Query()] = "active",
) -> Page[AccountOut]:
    return svc.list_accounts(db, principal, org_id, lq, status=status)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    org_id: uuid.UUID, body: AccountCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> AccountOut:
    return svc.create_account(db, principal, org_id, body)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    body: AccountUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> AccountOut:
    return svc.update_account(db, principal, org_id, account_id, body)


@router.post("/accounts/{account_id}/archive", response_model=AccountOut)
def archive_account(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> AccountOut:
    return svc.set_account_archived(db, principal, org_id, account_id, True)


@router.post("/accounts/{account_id}/restore", response_model=AccountOut)
def restore_account(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> AccountOut:
    return svc.set_account_archived(db, principal, org_id, account_id, False)


@router.get("/accounts/{account_id}/ledger", response_model=LedgerOut)
def account_ledger(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    currency: Annotated[str | None, Query(max_length=3)] = None,
) -> LedgerOut:
    return svc.ledger(
        db,
        principal,
        org_id,
        account_id,
        lq,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )


@router.get("/periods", response_model=list[PeriodOut])
def list_periods(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> list[PeriodOut]:
    return svc.list_periods(db, principal, org_id)


@router.post("/periods", response_model=PeriodOut, status_code=201)
def create_period(
    org_id: uuid.UUID, body: PeriodCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> PeriodOut:
    return svc.create_period(db, principal, org_id, body)


@router.post("/periods/{period_id}/close", response_model=PeriodOut)
def close_period(
    org_id: uuid.UUID, period_id: uuid.UUID, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> PeriodOut:
    return svc.set_period_status(db, principal, org_id, period_id, close=True)


@router.post("/periods/{period_id}/reopen", response_model=PeriodOut)
def reopen_period(
    org_id: uuid.UUID, period_id: uuid.UUID, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> PeriodOut:
    return svc.set_period_status(db, principal, org_id, period_id, close=False)


@router.get("/entries", response_model=Page[EntryListItem])
def list_entries(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    currency: Annotated[str | None, Query(max_length=3)] = None,
) -> Page[EntryListItem]:
    return svc.list_entries(
        db,
        principal,
        org_id,
        lq,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        currency=currency,
    )


@router.post("/entries", response_model=EntryOut, status_code=201)
def create_entry(
    org_id: uuid.UUID, body: EntryCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> EntryOut:
    return svc.create_entry(db, principal, org_id, body)


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(
    org_id: uuid.UUID, entry_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> EntryOut:
    return svc.get_entry(db, principal, org_id, entry_id)


@router.post("/entries/{entry_id}/reverse", response_model=EntryOut, status_code=201)
def reverse_entry(
    org_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: EntryReverse,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> EntryOut:
    return svc.reverse_entry(db, principal, org_id, entry_id, body)


@router.get("/reports/trial-balance", response_model=TrialBalanceOut)
def trial_balance(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    currency: Annotated[str | None, Query(max_length=3)] = None,
) -> TrialBalanceOut:
    return svc.trial_balance(
        db, principal, org_id, date_from=date_from, date_to=date_to, currency=currency
    )
