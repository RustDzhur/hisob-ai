import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.catalog import products as svc
from app.modules.catalog.schemas import ProductCreate, ProductOut, ProductUpdate
from app.modules.identity.deps import CurrentPrincipal
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/products", tags=["products"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("", response_model=Page[ProductOut])
def list_products(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    kind: Annotated[Literal["all", "goods", "service"], Query()] = "all",
    status: Annotated[Literal["active", "archived", "all"], Query()] = "active",
) -> Page[ProductOut]:
    return svc.list_products(db, principal, org_id, lq, kind=kind, status=status)


@router.post("", response_model=ProductOut, status_code=201)
def create_product(
    org_id: uuid.UUID, body: ProductCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> ProductOut:
    return svc.create_product(db, principal, org_id, body)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    org_id: uuid.UUID, product_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> ProductOut:
    return svc.get_product(db, principal, org_id, product_id)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    body: ProductUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> ProductOut:
    return svc.update_product(db, principal, org_id, product_id, body)


@router.post("/{product_id}/archive", response_model=ProductOut)
def archive_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> ProductOut:
    return svc.set_archived(db, principal, org_id, product_id, True)


@router.post("/{product_id}/restore", response_model=ProductOut)
def restore_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> ProductOut:
    return svc.set_archived(db, principal, org_id, product_id, False)
