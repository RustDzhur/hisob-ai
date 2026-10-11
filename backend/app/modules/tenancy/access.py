"""Единая проверка доступа к организации: членство -> контекст RLS -> право -> аудит отказа."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.identity import mfa
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.models import Membership, Organization
from app.platform.config import get_settings
from app.platform.db import set_context
from app.platform.errors import AppError


@dataclass(frozen=True)
class OrgAccess:
    org: Organization
    membership: Membership
    role_code: str
    permissions: frozenset[str]


def authorize(db: Session, principal: Principal, org_id: uuid.UUID, permission: str) -> OrgAccess:
    """Чужая, несуществующая организация и приостановленное членство неотличимы (404).

    Контекст организации для RLS ставится только после подтверждённого членства.
    """
    row = db.execute(
        select(Organization, Membership)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(
            Organization.id == org_id,
            Membership.user_id == principal.user.id,
            Membership.status == "active",
        )
    ).first()
    if row is None:
        raise AppError(404, "not_found", "Организация не найдена.")
    org, membership = row
    set_context(db, org_id=org.id)
    perms = rbac.permissions_of_role(db, membership.role_id)
    if get_settings().mfa_required_for_admins and rbac.role_code(db, membership.role_id) in (
        rbac.ROLE_OWNER,
        "org_admin",
    ):
        if not mfa.is_enabled(db, principal.user.id):
            raise AppError(
                403,
                "mfa_required_for_admins",
                "Для владельцев и администраторов обязателен второй фактор. Включите его в разделе «Безопасность».",
            )
    if permission not in perms:
        audit.record(
            db,
            action="access.denied",
            actor_user_id=principal.user.id,
            organization_id=org.id,
            after={"permission": permission},
        )
        db.commit()
        raise AppError(403, "forbidden", "Недостаточно прав для этого действия.")
    return OrgAccess(org, membership, rbac.role_code(db, membership.role_id), perms)
