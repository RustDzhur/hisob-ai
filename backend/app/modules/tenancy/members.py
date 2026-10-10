"""Use cases: участники и приглашения организации."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.identity.service import Principal, normalize_email
from app.modules.rbac import service as rbac
from app.modules.rbac.models import Role
from app.modules.tenancy.access import OrgAccess, authorize
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.schemas import (
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    InvitationPreview,
    MemberOut,
    MemberUpdate,
    OrganizationOut,
)
from app.platform.base import new_id, utcnow
from app.platform.config import get_settings
from app.platform.db import set_context
from app.platform.errors import AppError
from app.platform.security import hash_token, new_token

ROLE_OWNER = rbac.ROLE_OWNER
ROLE_ADMIN = "org_admin"


# ---------- правила (предложение Q8: org_admin управляет всем, кроме владельцев) ----------


def can_assign(actor_role: str, target_role: str) -> bool:
    if actor_role == ROLE_OWNER:
        return True
    return actor_role == ROLE_ADMIN and target_role != ROLE_OWNER


def can_modify_member(actor_role: str, member_role: str) -> bool:
    if actor_role == ROLE_OWNER:
        return True
    return actor_role == ROLE_ADMIN and member_role != ROLE_OWNER


def _forbidden() -> AppError:
    return AppError(403, "forbidden", "Недостаточно прав для этого действия.")


def _role_by_code(db: Session, code: str) -> Role:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise AppError(
            422,
            "unknown_role",
            "Неизвестная роль.",
            [{"field": "role_code", "message": "Неизвестная роль."}],
        )
    return role


def _invite_token_ctx(db: Session, token: str) -> None:
    """Владение токеном даёт доступ RLS ровно к одной строке приглашения (на эту транзакцию)."""
    db.execute(
        text("SELECT set_config('app.invite_token', :t, true)"), {"t": hash_token(token).hex()}
    )


# ---------- участники ----------


def _member_out(m: Membership, u: User, role_code: str) -> MemberOut:
    return MemberOut(
        id=m.id,
        user_id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=role_code,
        status=m.status,
        joined_at=m.joined_at,
    )


def list_members(db: Session, principal: Principal, org_id: uuid.UUID) -> list[MemberOut]:
    access = authorize(db, principal, org_id, rbac.MEMBERS_READ)
    rows = db.execute(
        select(Membership, User, Role.code)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.organization_id == access.org.id)
        .order_by(Membership.joined_at, Membership.id)
    ).all()
    return [_member_out(m, u, code) for m, u, code in rows]


def _active_owner_count(db: Session, org_id: uuid.UUID, owner_role_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.organization_id == org_id,
                Membership.role_id == owner_role_id,
                Membership.status == "active",
            )
        )
        or 0
    )


def update_member(
    db: Session, principal: Principal, org_id: uuid.UUID, member_id: uuid.UUID, data: MemberUpdate
) -> MemberOut:
    access = authorize(db, principal, org_id, rbac.MEMBERS_MANAGE)
    owner_role_id = rbac.get_role_id(db, ROLE_OWNER)

    # Блокируем все строки владельцев (в стабильном порядке) -> проверка «последний владелец»
    # не подвержена гонке между параллельными изменениями.
    db.execute(
        select(Membership.id)
        .where(Membership.organization_id == org_id, Membership.role_id == owner_role_id)
        .order_by(Membership.id)
        .with_for_update()
    ).all()
    target = db.scalar(
        select(Membership)
        .where(Membership.id == member_id, Membership.organization_id == org_id)
        .with_for_update()
    )
    if target is None:
        raise AppError(404, "not_found", "Участник не найден.")
    user = db.get(User, target.user_id)
    assert user is not None
    old_role = rbac.role_code(db, target.role_id)

    if not can_modify_member(access.role_code, old_role):
        raise _forbidden()

    new_role_id = target.role_id
    new_role = old_role
    if data.role_code is not None and data.role_code != old_role:
        role = _role_by_code(db, data.role_code)
        if not can_assign(access.role_code, role.code):
            raise _forbidden()
        new_role_id, new_role = role.id, role.code
    new_status = data.status or target.status

    if new_status == "suspended" and target.user_id == principal.user.id:
        raise AppError(422, "cannot_suspend_self", "Нельзя приостановить собственное участие.")

    if (new_role_id, new_status) == (target.role_id, target.status):
        return _member_out(target, user, old_role)

    was_active_owner = target.role_id == owner_role_id and target.status == "active"
    stays_active_owner = new_role_id == owner_role_id and new_status == "active"
    if was_active_owner and not stays_active_owner:
        if _active_owner_count(db, org_id, owner_role_id) <= 1:
            raise AppError(
                409, "last_owner", "В организации должен остаться хотя бы один активный владелец."
            )

    before = {"role": old_role, "status": target.status}
    target.role_id, target.status = new_role_id, new_status
    after = {"role": new_role, "status": new_status}
    if before["role"] != after["role"]:
        action = "member.role_changed"
    else:
        action = "member.suspended" if new_status == "suspended" else "member.reactivated"
    audit.record(
        db,
        action=action,
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="membership",
        object_id=target.id,
        before=before,
        after=after,
    )
    db.commit()
    return _member_out(target, user, new_role)


# ---------- приглашения ----------


def _invitation_out(inv: Invitation, role_code: str) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        role=role_code,
        expires_at=inv.expires_at,
        expired=inv.expires_at <= utcnow(),
        created_at=inv.created_at,
    )


def create_invitation(
    db: Session, principal: Principal, org_id: uuid.UUID, data: InvitationCreate
) -> InvitationCreated:
    access = authorize(db, principal, org_id, rbac.MEMBERS_MANAGE)
    role = _role_by_code(db, data.role_code)
    if not can_assign(access.role_code, role.code):
        raise _forbidden()
    email = normalize_email(data.email)

    already = db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == org_id, User.email == email)
    )
    if already is not None:
        raise AppError(409, "already_member", "Этот пользователь уже участник организации.")

    token = new_token()
    inv = Invitation(
        id=new_id(),
        organization_id=org_id,
        email=email,
        role_id=role.id,
        token_hash=hash_token(token),
        invited_by=principal.user.id,
        expires_at=utcnow() + timedelta(days=get_settings().invitation_ttl_days),
    )
    db.add(inv)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409, "invitation_exists", "Для этого email уже есть действующее приглашение."
        ) from exc
    audit.record(
        db,
        action="member.invited",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="invitation",
        object_id=inv.id,
        after={"email": email, "role": role.code, "expires_at": inv.expires_at.isoformat()},
    )
    db.commit()
    base = _invitation_out(inv, role.code)
    return InvitationCreated(**base.model_dump(), token=token)


def list_invitations(db: Session, principal: Principal, org_id: uuid.UUID) -> list[InvitationOut]:
    authorize(db, principal, org_id, rbac.MEMBERS_MANAGE)
    rows = db.execute(
        select(Invitation, Role.code)
        .join(Role, Role.id == Invitation.role_id)
        .where(
            Invitation.organization_id == org_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .order_by(Invitation.created_at.desc())
    ).all()
    return [_invitation_out(i, code) for i, code in rows]


def revoke_invitation(
    db: Session, principal: Principal, org_id: uuid.UUID, invitation_id: uuid.UUID
) -> None:
    authorize(db, principal, org_id, rbac.MEMBERS_MANAGE)
    inv = db.scalar(
        select(Invitation)
        .where(
            Invitation.id == invitation_id,
            Invitation.organization_id == org_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if inv is None:
        raise AppError(404, "not_found", "Приглашение не найдено.")
    inv.revoked_at = utcnow()
    audit.record(
        db,
        action="member.invitation_revoked",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="invitation",
        object_id=inv.id,
        after={"email": inv.email},
    )
    db.commit()


def _state(inv: Invitation) -> str:
    if inv.accepted_at is not None:
        return "accepted"
    if inv.revoked_at is not None:
        return "revoked"
    return "expired" if inv.expires_at <= utcnow() else "pending"


def preview_invitation(db: Session, token: str) -> InvitationPreview:
    _invite_token_ctx(db, token)
    row = db.execute(
        select(Invitation, Organization.name, Role.code)
        .join(Organization, Organization.id == Invitation.organization_id)
        .join(Role, Role.id == Invitation.role_id)
        .where(Invitation.token_hash == hash_token(token))
    ).first()
    if row is None:
        raise AppError(404, "invitation_not_found", "Приглашение не найдено.")
    inv, org_name, role_code = row
    return InvitationPreview(
        organization_name=org_name,
        role=role_code,
        email=inv.email,
        state=_state(inv),
    )


def accept_invitation(db: Session, principal: Principal, token: str) -> OrganizationOut:
    _invite_token_ctx(db, token)
    inv = db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token)).with_for_update()
    )
    if inv is None:
        raise AppError(404, "invitation_not_found", "Приглашение не найдено.")
    state = _state(inv)
    if state == "accepted":
        raise AppError(409, "invitation_used", "Приглашение уже использовано.")
    if state == "revoked":
        raise AppError(410, "invitation_revoked", "Приглашение отозвано.")
    if state == "expired":
        raise AppError(410, "invitation_expired", "Срок действия приглашения истёк.")
    if normalize_email(principal.user.email) != normalize_email(inv.email):
        raise AppError(
            403,
            "invitation_wrong_user",
            "Приглашение выдано на другой email. Войдите под нужным аккаунтом.",
        )

    set_context(db, org_id=inv.organization_id)
    exists = db.scalar(
        select(Membership.id).where(
            Membership.organization_id == inv.organization_id,
            Membership.user_id == principal.user.id,
        )
    )
    if exists is not None:
        raise AppError(409, "already_member", "Вы уже участник этой организации.")

    db.add(
        Membership(
            id=new_id(),
            organization_id=inv.organization_id,
            user_id=principal.user.id,
            role_id=inv.role_id,
            status="active",
        )
    )
    inv.accepted_at = utcnow()
    role_code = rbac.role_code(db, inv.role_id)
    audit.record(
        db,
        action="member.joined",
        actor_user_id=principal.user.id,
        organization_id=inv.organization_id,
        object_type="invitation",
        object_id=inv.id,
        after={"role": role_code},
    )
    org = db.get(Organization, inv.organization_id)
    assert org is not None
    perms = rbac.permissions_of_role(db, inv.role_id)
    out = OrganizationOut(
        id=org.id,
        name=org.name,
        tin=org.tin,
        timezone=org.timezone,
        base_currency=org.base_currency,
        role=role_code,
        permissions=sorted(perms),
    )
    db.commit()
    return out


__all__ = ["OrgAccess"]
