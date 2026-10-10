import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.modules.catalog import currencies, units
from app.modules.catalog.schemas import UnitCreate, UnitOut, UnitUpdate
from app.modules.identity.deps import CurrentPrincipal
from app.modules.tenancy.schemas import CurrencyOut, CurrencyToggle
from app.platform.csrf import require_csrf
from app.platform.db import get_db

router = APIRouter(prefix="/organizations/{org_id}/units", tags=["catalog"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("", response_model=list[UnitOut])
def list_units(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> list[UnitOut]:
    return units.list_units(db, principal, org_id)


@router.post("", response_model=UnitOut, status_code=201)
def create_unit(
    org_id: uuid.UUID, body: UnitCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> UnitOut:
    return units.create_unit(db, principal, org_id, body)


@router.patch("/{unit_id}", response_model=UnitOut)
def update_unit(
    org_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> UnitOut:
    return units.update_unit(db, principal, org_id, unit_id, body)


currencies_router = APIRouter(prefix="/organizations/{org_id}/currencies", tags=["catalog"])


@currencies_router.get("", response_model=list[CurrencyOut])
def list_currencies(
    org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> list[CurrencyOut]:
    return currencies.list_currencies(db, principal, org_id)


@currencies_router.put("/{code}", response_model=list[CurrencyOut])
def set_currency(
    org_id: uuid.UUID,
    code: Annotated[str, Path(min_length=3, max_length=3)],
    body: CurrencyToggle,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> list[CurrencyOut]:
    return currencies.set_enabled(db, principal, org_id, code, body.enabled)
