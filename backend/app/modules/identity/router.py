import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.modules.identity import service
from app.modules.identity.deps import SESSION_COOKIE, CurrentPrincipal
from app.modules.identity.schemas import ChangePasswordIn, LoginIn, RegisterIn, SessionOut, UserOut
from app.platform.config import get_settings
from app.platform.csrf import CSRF_COOKIE, require_csrf
from app.platform.db import get_db
from app.platform.security import new_token

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]


def _set_auth_cookies(response: Response, session_token: str) -> None:
    s = get_settings()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
    # CSRF-cookie читается клиентским кодом и возвращается в заголовке X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE,
        new_token(),
        max_age=max_age,
        httponly=False,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, response: Response, db: DbSession) -> UserOut:
    user, token = service.register(
        db, email=body.email, password=body.password, full_name=body.full_name
    )
    _set_auth_cookies(response, token)
    return UserOut.model_validate(user)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: DbSession) -> UserOut:
    user, token = service.login(db, email=body.email, password=body.password)
    _set_auth_cookies(response, token)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    principal: CurrentPrincipal,
    _csrf: Annotated[None, Depends(require_csrf)],
    db: DbSession,
) -> None:
    service.logout(db, principal)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(principal: CurrentPrincipal) -> UserOut:
    return UserOut.model_validate(principal.user)


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordIn,
    principal: CurrentPrincipal,
    _csrf: Annotated[None, Depends(require_csrf)],
    db: DbSession,
) -> None:
    service.change_password(db, principal, current=body.current_password, new=body.new_password)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(principal: CurrentPrincipal, db: DbSession) -> list[SessionOut]:
    return service.list_sessions(db, principal)


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: uuid.UUID,
    response: Response,
    principal: CurrentPrincipal,
    _csrf: Annotated[None, Depends(require_csrf)],
    db: DbSession,
) -> None:
    service.revoke_session(db, principal, session_id)
    if session_id == principal.session_id:
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/logout-all", status_code=204)
def logout_all(
    response: Response,
    principal: CurrentPrincipal,
    _csrf: Annotated[None, Depends(require_csrf)],
    db: DbSession,
) -> None:
    service.logout_all(db, principal)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
