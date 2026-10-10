import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.identity.deps import CurrentPrincipal
from app.modules.inventory import warehouses as svc
from app.modules.inventory.schemas import WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/warehouses", tags=["warehouses"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("", response_model=Page[WarehouseOut])
def list_warehouses(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[Literal["active", "archived", "all"], Query()] = "active",
) -> Page[WarehouseOut]:
    return svc.list_warehouses(db, principal, org_id, lq, status=status)


@router.post("", response_model=WarehouseOut, status_code=201)
def create_warehouse(
    org_id: uuid.UUID,
    body: WarehouseCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> WarehouseOut:
    return svc.create_warehouse(db, principal, org_id, body)


@router.get("/{warehouse_id}", response_model=WarehouseOut)
def get_warehouse(
    org_id: uuid.UUID, warehouse_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> WarehouseOut:
    return svc.get_warehouse(db, principal, org_id, warehouse_id)


@router.patch("/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(
    org_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    body: WarehouseUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> WarehouseOut:
    return svc.update_warehouse(db, principal, org_id, warehouse_id, body)


@router.post("/{warehouse_id}/archive", response_model=WarehouseOut)
def archive_warehouse(
    org_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> WarehouseOut:
    return svc.set_archived(db, principal, org_id, warehouse_id, True)


@router.post("/{warehouse_id}/restore", response_model=WarehouseOut)
def restore_warehouse(
    org_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> WarehouseOut:
    return svc.set_archived(db, principal, org_id, warehouse_id, False)
