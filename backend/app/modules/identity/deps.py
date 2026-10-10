from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.modules.identity import service
from app.platform.db import get_db, set_context
from app.platform.errors import AppError

SESSION_COOKIE = "hisob_session"


def get_principal(request: Request, db: Annotated[Session, Depends(get_db)]) -> service.Principal:
    token = request.cookies.get(SESSION_COOKIE)
    principal = service.resolve_session(db, token) if token else None
    if principal is None:
        raise AppError(401, "not_authenticated", "Требуется вход.")
    set_context(db, user_id=principal.user.id)
    return principal


CurrentPrincipal = Annotated[service.Principal, Depends(get_principal)]
