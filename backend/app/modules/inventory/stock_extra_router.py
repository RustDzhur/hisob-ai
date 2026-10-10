import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.modules.identity.deps import CurrentPrincipal
from app.modules.inventory import counts, reservations
from app.modules.inventory.schemas import (
    CountCreate,
    CountListItem,
    CountOut,
    CountUpdate,
    MinLevelIn,
    OperationAction,
    ReservationCreate,
    ReservationOut,
)
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/stock", tags=["stock"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


# ---------------- инвентаризация ----------------


@router.get("/counts", response_model=Page[CountListItem])
def list_counts(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[Literal["all", "IN_PROGRESS", "POSTED"], Query()] = "all",
) -> Page[CountListItem]:
    return counts.list_counts(db, principal, org_id, lq, status=status)


@router.post("/counts", response_model=CountOut, status_code=201)
def create_count(
    org_id: uuid.UUID, body: CountCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> CountOut:
    return counts.create_count(db, principal, org_id, body)


@router.get("/counts/{count_id}", response_model=CountOut)
def get_count(
    org_id: uuid.UUID, count_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> CountOut:
    return counts.get_count(db, principal, org_id, count_id)


@router.patch("/counts/{count_id}", response_model=CountOut)
def update_count(
    org_id: uuid.UUID,
    count_id: uuid.UUID,
    body: CountUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> CountOut:
    return counts.update_count(db, principal, org_id, count_id, body)


@router.delete("/counts/{count_id}", status_code=204)
def delete_count(
    org_id: uuid.UUID,
    count_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    counts.delete_count(db, principal, org_id, count_id, expected_version)
    return Response(status_code=204)


@router.post("/counts/{count_id}/post", response_model=CountOut)
def post_count(
    org_id: uuid.UUID,
    count_id: uuid.UUID,
    body: OperationAction,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> CountOut:
    return counts.post_count(db, principal, org_id, count_id, body.expected_version)


# ---------------- минимальный запас и резервы ----------------


@router.put("/min-levels", status_code=204)
def put_min_level(
    org_id: uuid.UUID, body: MinLevelIn, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> Response:
    reservations.set_min_level(db, principal, org_id, body)
    return Response(status_code=204)


@router.get("/reservations", response_model=Page[ReservationOut])
def list_reservations(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[Literal["all", "ACTIVE", "RELEASED"], Query()] = "ACTIVE",
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    product_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[ReservationOut]:
    return reservations.list_reservations(
        db, principal, org_id, lq, status=status, warehouse_id=warehouse_id, product_id=product_id
    )


@router.post("/reservations", response_model=ReservationOut, status_code=201)
def create_reservation(
    org_id: uuid.UUID,
    body: ReservationCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> ReservationOut:
    return reservations.create_reservation(db, principal, org_id, body)


@router.post("/reservations/{reservation_id}/release", response_model=ReservationOut)
def release_reservation(
    org_id: uuid.UUID,
    reservation_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> ReservationOut:
    return reservations.release_reservation(db, principal, org_id, reservation_id)
