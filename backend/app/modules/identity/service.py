"""Use cases identity: регистрация, вход, выход, разбор сессии."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.identity.models import LoginAttempt, User, UserSession
from app.modules.identity.policy import validate_password
from app.modules.identity.schemas import SessionOut
from app.platform import context
from app.platform.base import new_id, utcnow
from app.platform.config import get_settings
from app.platform.db import set_context
from app.platform.errors import AppError
from app.platform.security import (
    DUMMY_HASH,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: uuid.UUID


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _email_taken() -> AppError:
    return AppError(
        409,
        "email_taken",
        "Пользователь с таким email уже зарегистрирован.",
        [{"field": "email", "code": "email_taken", "message": "Email уже используется."}],
    )


def _open_session(db: Session, user: User) -> str:
    token = new_token()
    db.add(
        UserSession(
            id=new_id(),
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
            ip=context.client_ip.get(),
            user_agent=context.user_agent.get(),
        )
    )
    return token


def register(db: Session, *, email: str, password: str, full_name: str) -> tuple[User, str]:
    settings = get_settings()
    email = normalize_email(email)
    validate_password(password, email=email, min_length=settings.password_min_length)

    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise _email_taken()

    user = User(
        id=new_id(),
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:  # гонка двух регистраций с одним email
        db.rollback()
        raise _email_taken() from exc

    set_context(db, user_id=user.id)
    token = _open_session(db, user)
    audit.record(
        db,
        action="auth.registered",
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
        after={"email": email, "full_name": user.full_name},
    )
    audit.record(
        db, action="auth.login", actor_user_id=user.id, object_type="user", object_id=user.id
    )
    db.add(LoginAttempt(id=new_id(), email=email, ip=context.client_ip.get(), succeeded=True))
    user.last_login_at = utcnow()
    db.commit()
    return user, token


def _is_throttled(db: Session, email: str) -> bool:
    s = get_settings()
    window_start = utcnow() - timedelta(minutes=s.login_window_minutes)
    last_success = db.scalar(
        select(func.max(LoginAttempt.attempted_at)).where(
            LoginAttempt.email == email, LoginAttempt.succeeded.is_(True)
        )
    )
    since = max(window_start, last_success) if last_success else window_start
    failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.attempted_at >= since,
        )
    )
    return (failures or 0) >= s.login_max_failures


def login(db: Session, *, email: str, password: str) -> tuple[User, str]:
    settings = get_settings()
    email = normalize_email(email)

    if _is_throttled(db, email):
        raise AppError(
            429,
            "too_many_attempts",
            "Слишком много неудачных попыток входа. Попробуйте позже.",
            headers={"Retry-After": str(settings.login_window_minutes * 60)},
        )

    user = db.scalar(select(User).where(User.email == email))
    # Всегда проверяем хэш, чтобы время ответа не выдавало существование email.
    ok = verify_password(user.password_hash if user else DUMMY_HASH, password)
    if user is None or not ok or not user.is_active:
        db.add(LoginAttempt(id=new_id(), email=email, ip=context.client_ip.get(), succeeded=False))
        audit.record(
            db,
            action="auth.login_failed",
            actor_user_id=None,
            after={"email": email},
        )
        db.commit()
        raise AppError(401, "invalid_credentials", "Неверный email или пароль.")

    set_context(db, user_id=user.id)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    token = _open_session(db, user)
    db.add(LoginAttempt(id=new_id(), email=email, ip=context.client_ip.get(), succeeded=True))
    audit.record(
        db, action="auth.login", actor_user_id=user.id, object_type="user", object_id=user.id
    )
    db.commit()
    return user, token


def logout(db: Session, principal: Principal) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.id == principal.session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    audit.record(
        db,
        action="auth.logout",
        actor_user_id=principal.user.id,
        object_type="user",
        object_id=principal.user.id,
    )
    db.commit()


def resolve_session(db: Session, token: str) -> Principal | None:
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    sess, user = row
    if sess.revoked_at is not None or sess.expires_at <= utcnow() or not user.is_active:
        return None
    return Principal(user=user, session_id=sess.id)


def _revoke_sessions(db: Session, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None) -> int:
    stmt = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_id is not None:
        stmt = stmt.where(UserSession.id != except_id)
    return int(db.execute(stmt.values(revoked_at=utcnow())).rowcount)  # type: ignore[attr-defined]


def change_password(db: Session, principal: Principal, *, current: str, new: str) -> None:
    """Смена пароля: проверка текущего (с тем же лимитом попыток, что и вход), завершение прочих сессий."""
    settings = get_settings()
    user = principal.user
    if _is_throttled(db, user.email):
        raise AppError(
            429,
            "too_many_attempts",
            "Слишком много неудачных попыток. Попробуйте позже.",
            headers={"Retry-After": str(settings.login_window_minutes * 60)},
        )
    if not verify_password(user.password_hash, current):
        db.add(
            LoginAttempt(id=new_id(), email=user.email, ip=context.client_ip.get(), succeeded=False)
        )
        audit.record(
            db,
            action="auth.password_change_failed",
            actor_user_id=user.id,
            object_type="user",
            object_id=user.id,
        )
        db.commit()
        raise AppError(
            422,
            "invalid_current_password",
            "Текущий пароль указан неверно.",
            [
                {
                    "field": "current_password",
                    "code": "invalid_current_password",
                    "message": "Неверный пароль.",
                }
            ],
        )
    validate_password(
        new, email=user.email, min_length=settings.password_min_length, field="new_password"
    )
    if new == current:
        raise AppError(
            422,
            "weak_password",
            "Новый пароль должен отличаться от текущего.",
            [
                {
                    "field": "new_password",
                    "code": "password_same_as_current",
                    "message": "Новый пароль должен отличаться от текущего.",
                }
            ],
        )
    user.password_hash = hash_password(new)
    revoked = _revoke_sessions(db, user.id, except_id=principal.session_id)
    audit.record(
        db,
        action="auth.password_changed",
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
        after={"revoked_other_sessions": revoked},
    )
    db.commit()


def list_sessions(db: Session, principal: Principal) -> list[SessionOut]:
    rows = db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == principal.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.created_at.desc())
    )
    return [
        SessionOut(
            id=r.id,
            created_at=r.created_at,
            expires_at=r.expires_at,
            ip=r.ip,
            user_agent=r.user_agent,
            current=r.id == principal.session_id,
        )
        for r in rows
    ]


def revoke_session(db: Session, principal: Principal, session_id: uuid.UUID) -> None:
    n = db.execute(
        update(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == principal.user.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    ).rowcount  # type: ignore[attr-defined]
    if n == 0:
        raise AppError(404, "not_found", "Сессия не найдена.")
    audit.record(
        db,
        action="auth.session_revoked",
        actor_user_id=principal.user.id,
        object_type="session",
        object_id=session_id,
    )
    db.commit()


def logout_all(db: Session, principal: Principal) -> None:
    n = _revoke_sessions(db, principal.user.id)
    audit.record(
        db,
        action="auth.logout_all",
        actor_user_id=principal.user.id,
        object_type="user",
        object_id=principal.user.id,
        after={"revoked_sessions": n},
    )
    db.commit()
