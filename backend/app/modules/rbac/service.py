import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.rbac.models import Role, RolePermission

# Коды прав — константы кода; роли и их состав — данные в БД (заполняются миграцией).
ORG_READ = "org.read"
ORG_MANAGE = "org.manage"
MEMBERS_READ = "members.read"
MEMBERS_MANAGE = "members.manage"
AUDIT_READ = "audit.read"
CATALOG_READ = "catalog.read"
CATALOG_MANAGE = "catalog.manage"
WAREHOUSES_READ = "warehouses.read"
WAREHOUSES_MANAGE = "warehouses.manage"

ROLE_OWNER = "owner"


def get_role_id(db: Session, code: str) -> uuid.UUID:
    role_id = db.scalar(select(Role.id).where(Role.code == code))
    if role_id is None:
        raise RuntimeError(f"роль {code!r} не найдена: не применены миграции?")
    return role_id


def role_code(db: Session, role_id: uuid.UUID) -> str:
    return db.scalar(select(Role.code).where(Role.id == role_id)) or ""


def permissions_of_role(db: Session, role_id: uuid.UUID) -> frozenset[str]:
    codes = db.scalars(
        select(RolePermission.permission_code).where(RolePermission.role_id == role_id)
    )
    return frozenset(codes)
