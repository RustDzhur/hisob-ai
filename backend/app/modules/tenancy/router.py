import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.modules.identity.deps import CurrentPrincipal
from app.modules.rbac.service import ORG_READ
from app.modules.tenancy import audit_view, members, service
from app.modules.tenancy.schemas import (
    AuditPage,
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    InvitationPreview,
    MemberOut,
    MemberUpdate,
    OrganizationCreate,
    OrganizationOut,
)
from app.platform.csrf import require_csrf
from app.platform.db import get_db

router = APIRouter(prefix="/organizations", tags=["organizations"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=OrganizationOut, status_code=201)
def create_organization(
    body: OrganizationCreate,
    principal: CurrentPrincipal,
    _csrf: Annotated[None, Depends(require_csrf)],
    db: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    payload, status, replayed = service.create_organization(db, principal, body, idempotency_key)
    headers = {"Idempotent-Replayed": "true"} if replayed else {}
    return JSONResponse(payload, status_code=status, headers=headers)


@router.get("", response_model=list[OrganizationOut])
def list_organizations(principal: CurrentPrincipal, db: DbSession) -> list[OrganizationOut]:
    return service.list_organizations(db, principal)


@router.get("/{org_id}", response_model=OrganizationOut)
def get_organization(
    org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> OrganizationOut:
    return service.get_organization(db, principal, org_id, ORG_READ)


# ---------- участники и приглашения ----------

Csrf = Annotated[None, Depends(require_csrf)]


@router.get("/{org_id}/members", response_model=list[MemberOut])
def list_members(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> list[MemberOut]:
    return members.list_members(db, principal, org_id)


@router.patch("/{org_id}/members/{member_id}", response_model=MemberOut)
def update_member(
    org_id: uuid.UUID,
    member_id: uuid.UUID,
    body: MemberUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> MemberOut:
    return members.update_member(db, principal, org_id, member_id, body)


@router.get("/{org_id}/invitations", response_model=list[InvitationOut])
def list_invitations(
    org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> list[InvitationOut]:
    return members.list_invitations(db, principal, org_id)


@router.post("/{org_id}/invitations", response_model=InvitationCreated, status_code=201)
def create_invitation(
    org_id: uuid.UUID,
    body: InvitationCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> InvitationCreated:
    return members.create_invitation(db, principal, org_id, body)


@router.delete("/{org_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    org_id: uuid.UUID,
    invitation_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> None:
    members.revoke_invitation(db, principal, org_id, invitation_id)


@router.get("/{org_id}/audit", response_model=AuditPage)
def list_audit(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=audit_view.MAX_PAGE_SIZE)] = 25,
    action: Annotated[str | None, Query(max_length=100)] = None,
    actor_user_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AuditPage:
    return audit_view.list_events(
        db,
        principal,
        org_id,
        page=page,
        page_size=page_size,
        action=action,
        actor_user_id=actor_user_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/{org_id}/audit/actions", response_model=list[str])
def list_audit_actions(org_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> list[str]:
    return audit_view.list_actions(db, principal, org_id)


invitations_router = APIRouter(prefix="/invitations", tags=["invitations"])


@invitations_router.get("/{token}", response_model=InvitationPreview)
def preview_invitation(token: str, db: DbSession) -> InvitationPreview:
    """Публичный просмотр по токену (владение токеном = доступ)."""
    return members.preview_invitation(db, token)


@invitations_router.post("/accept", response_model=OrganizationOut)
def accept_invitation(
    body: InvitationAccept, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> OrganizationOut:
    return members.accept_invitation(db, principal, body.token)
