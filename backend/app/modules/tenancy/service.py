import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.schemas import OrganizationCreate, OrganizationOut
from app.platform import idempotency
from app.platform.base import new_id
from app.platform.db import set_context


def _out(org: Organization, role_code: str, permissions: frozenset[str]) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        name=org.name,
        tin=org.tin,
        timezone=org.timezone,
        base_currency=org.base_currency,
        role=role_code,
        permissions=sorted(permissions),
    )


def create_organization(
    db: Session, principal: Principal, data: OrganizationCreate, idempotency_key: str | None
) -> tuple[dict[str, Any], int, bool]:
    """Создаёт организацию, делает автора владельцем. Возвращает (тело, статус, replayed)."""
    key = idempotency.validate_key(idempotency_key)
    user = principal.user
    req_hash = idempotency.request_hash(data.model_dump(mode="json"))

    saved = idempotency.find(db, user.id, key, req_hash)
    if saved is not None:
        return saved.response_body, saved.response_status, True

    owner_role_id = rbac.get_role_id(db, rbac.ROLE_OWNER)
    org = Organization(
        id=new_id(),
        name=data.name.strip(),
        tin=data.tin,
        timezone=data.timezone,
        base_currency=data.base_currency,
    )
    db.add(org)
    db.flush()

    # С этого момента RLS видит новую организацию как текущую.
    set_context(db, user_id=user.id, org_id=org.id)
    db.add(
        Membership(
            id=new_id(),
            organization_id=org.id,
            user_id=user.id,
            role_id=owner_role_id,
            status="active",
        )
    )
    audit.record(
        db,
        action="org.created",
        actor_user_id=user.id,
        organization_id=org.id,
        object_type="organization",
        object_id=org.id,
        after={
            "name": org.name,
            "tin": org.tin,
            "timezone": org.timezone,
            "base_currency": org.base_currency,
            "owner_user_id": str(user.id),
        },
    )
    body = _out(org, rbac.ROLE_OWNER, rbac.permissions_of_role(db, owner_role_id)).model_dump(
        mode="json"
    )
    idempotency.store(db, user.id, key, req_hash, 201, body)
    db.commit()
    return body, 201, False


def list_organizations(db: Session, principal: Principal) -> list[OrganizationOut]:
    rows = db.execute(
        select(Organization, Membership.role_id)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == principal.user.id, Membership.status == "active")
        .order_by(Organization.name)
    ).all()
    return [
        _out(org, rbac.role_code(db, role_id), rbac.permissions_of_role(db, role_id))
        for org, role_id in rows
    ]


def get_organization(
    db: Session, principal: Principal, org_id: uuid.UUID, permission: str
) -> OrganizationOut:
    access = authorize(db, principal, org_id, permission)
    return _out(access.org, access.role_code, access.permissions)
