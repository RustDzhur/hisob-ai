import uuid

from sqlalchemy import Boolean, ForeignKey, Text, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.base import Base, new_id


class Role(Base):
    __tablename__ = "role"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Permission(Base):
    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text)


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permission.code"), primary_key=True)
