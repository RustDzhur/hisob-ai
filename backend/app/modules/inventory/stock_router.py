import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.modules.identity.deps import CurrentPrincipal
from app.modules.inventory import stock as svc
from app.modules.inventory.schemas import (
    BalanceOut,
    FromDocumentIn,
    MovementOut,
    OperationAction,
    OperationCreate,
    OperationListItem,
    OperationOut,
    OperationUpdate,
    ReconciliationOut,
    ReverseIn,
    StockSettings,
)
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/stock", tags=["stock"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("/settings", response_model=StockSettings)
def get_settings(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> StockSettings:
    return svc.get_settings(db, principal, org_id)


@router.put("/settings", response_model=StockSettings)
def put_settings(
    org_id: uuid.UUID, body: StockSettings, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> StockSettings:
    return svc.update_settings(db, principal, org_id, body)


@router.get("/balances", response_model=Page[BalanceOut])
def balances(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    include_zero: Annotated[bool, Query()] = False,
) -> Page[BalanceOut]:
    return svc.list_balances(
        db, principal, org_id, lq, warehouse_id=warehouse_id, include_zero=include_zero
    )


@router.get("/movements", response_model=Page[MovementOut])
def movements(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    product_id: Annotated[uuid.UUID | None, Query()] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[MovementOut]:
    return svc.list_movements(
        db, principal, org_id, lq, product_id=product_id, warehouse_id=warehouse_id
    )


@router.get("/reconciliation", response_model=ReconciliationOut)
def reconciliation(
    org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> ReconciliationOut:
    return svc.reconciliation(db, principal, org_id)


@router.get("/operations", response_model=Page[OperationListItem])
def list_operations(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[Literal["all", "DRAFT", "POSTED"], Query()] = "all",
    kind: Annotated[
        Literal["receipt", "issue", "transfer", "supplier_return", "customer_return"] | None,
        Query(),
    ] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    document_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[OperationListItem]:
    return svc.list_operations(
        db,
        principal,
        org_id,
        lq,
        status=status,
        kind=kind,
        warehouse_id=warehouse_id,
        document_id=document_id,
    )


@router.post("/operations", response_model=OperationOut, status_code=201)
def create_operation(
    org_id: uuid.UUID,
    body: OperationCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> OperationOut:
    return svc.create_operation(db, principal, org_id, body)


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(
    org_id: uuid.UUID, operation_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> OperationOut:
    return svc.get_operation(db, principal, org_id, operation_id)


@router.patch("/operations/{operation_id}", response_model=OperationOut)
def update_operation(
    org_id: uuid.UUID,
    operation_id: uuid.UUID,
    body: OperationUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> OperationOut:
    return svc.update_operation(db, principal, org_id, operation_id, body)


@router.delete("/operations/{operation_id}", status_code=204)
def delete_operation(
    org_id: uuid.UUID,
    operation_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    svc.delete_operation(db, principal, org_id, operation_id, expected_version)
    return Response(status_code=204)


@router.post("/operations/{operation_id}/post", response_model=OperationOut)
def post_operation(
    org_id: uuid.UUID,
    operation_id: uuid.UUID,
    body: OperationAction,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> OperationOut:
    return svc.post_operation(db, principal, org_id, operation_id, body.expected_version)


@router.post("/operations/from-document", response_model=OperationOut, status_code=201)
def create_from_document(
    org_id: uuid.UUID,
    body: FromDocumentIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> OperationOut:
    return svc.create_from_document(db, principal, org_id, body)


@router.post("/operations/{operation_id}/reverse", response_model=OperationOut)
def reverse_operation(
    org_id: uuid.UUID,
    operation_id: uuid.UUID,
    body: ReverseIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> OperationOut:
    return svc.reverse_operation(db, principal, org_id, operation_id, body)
