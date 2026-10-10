from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.deps import CurrentPrincipal
from app.modules.rbac.models import Role
from app.platform.db import get_db

router = APIRouter(prefix="/roles", tags=["roles"])


class RoleOut(BaseModel):
    code: str
    name: str


@router.get("", response_model=list[RoleOut])
def list_roles(_: CurrentPrincipal, db: Annotated[Session, Depends(get_db)]) -> list[RoleOut]:
    """Справочник системных ролей (одинаков для всех организаций)."""
    rows = db.execute(select(Role.code, Role.name).order_by(Role.name)).all()
    return [RoleOut(code=c, name=n) for c, n in rows]
