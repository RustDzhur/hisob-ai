"""Второй фактор: настройка, подтверждение, коды восстановления, проверка при входе."""

import hashlib
import secrets
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.identity import totp
from app.modules.identity.models import LoginAttempt, MfaRecoveryCode, User, UserMfa
from app.modules.identity.service import Principal, _is_throttled
from app.platform import context, crypto
from app.platform.base import new_id, utcnow
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.security import verify_password

RECOVERY_CODES = 8


def _err(status: int, code: str, message: str) -> AppError:
    return AppError(status, code, message)


def _row(db: Session, user_id: Any) -> UserMfa | None:
    return db.get(UserMfa, user_id)


def is_enabled(db: Session, user_id: Any) -> bool:
    r = _row(db, user_id)
    return bool(r and r.enabled)


def _hash(code: str) -> str:
    return hashlib.sha256(
        code.strip().lower().replace("-", "").replace(" ", "").encode()
    ).hexdigest()


def _new_recovery_codes(db: Session, user_id: Any) -> list[str]:
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)  # 10 символов
        codes.append(f"{raw[:5]}-{raw[5:]}")
        db.add(MfaRecoveryCode(id=new_id(), user_id=user_id, code_hash=_hash(raw)))
    return codes


def status(db: Session, principal: Principal) -> dict[str, Any]:
    r = _row(db, principal.user.id)
    left = 0
    if r and r.enabled:
        left = len(
            db.scalars(
                select(MfaRecoveryCode.id).where(
                    MfaRecoveryCode.user_id == principal.user.id, MfaRecoveryCode.used_at.is_(None)
                )
            ).all()
        )
    return {
        "available": crypto.available(),
        "enabled": bool(r and r.enabled),
        "pending": bool(r and not r.enabled),
        "recovery_codes_left": left,
        "required_for_admins": get_settings().mfa_required_for_admins,
    }


def setup(db: Session, principal: Principal) -> dict[str, str]:
    if not crypto.available():
        raise _err(
            409, "mfa_unavailable", "Второй фактор недоступен: на сервере не задан ключ шифрования."
        )
    user = principal.user
    r = _row(db, user.id)
    if r and r.enabled:
        raise _err(409, "mfa_already_enabled", "Второй фактор уже включён.")
    secret = totp.new_secret()
    if r is None:
        db.add(UserMfa(user_id=user.id, secret_enc=crypto.encrypt(secret)))
    else:
        r.secret_enc, r.last_used_step = crypto.encrypt(secret), None
    audit.record(
        db,
        action="auth.mfa_setup_started",
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
    )
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp.otpauth_uri(secret, user.email, get_settings().mfa_issuer),
    }


def _fail_attempt(db: Session, user: User) -> None:
    db.add(LoginAttempt(id=new_id(), email=user.email, ip=context.client_ip.get(), succeeded=False))


def _throttle(db: Session, user: User) -> None:
    if _is_throttled(db, user.email):
        s = get_settings()
        raise AppError(
            429,
            "too_many_attempts",
            "Слишком много неудачных попыток. Попробуйте позже.",
            headers={"Retry-After": str(s.login_window_minutes * 60)},
        )


def enable(db: Session, principal: Principal, code: str) -> list[str]:
    user = principal.user
    _throttle(db, user)
    r = _row(db, user.id)
    secret = crypto.decrypt(r.secret_enc) if r else None
    if r is None or r.enabled or secret is None:
        raise _err(409, "mfa_not_pending", "Сначала начните настройку второго фактора.")
    step = totp.verify(secret, code, None)
    if step is None:
        _fail_attempt(db, user)
        audit.record(
            db,
            action="auth.mfa_failed",
            actor_user_id=user.id,
            object_type="user",
            object_id=user.id,
            after={"stage": "enable"},
        )
        db.commit()
        raise _err(422, "mfa_invalid_code", "Неверный код.")
    r.enabled, r.confirmed_at, r.last_used_step = True, utcnow(), step
    codes = _new_recovery_codes(db, user.id)
    audit.record(
        db, action="auth.mfa_enabled", actor_user_id=user.id, object_type="user", object_id=user.id
    )
    db.commit()
    return codes


def _check_second_factor(db: Session, user: User, code: str) -> str:
    """Проверяет TOTP или код восстановления. Возвращает 'totp'|'recovery' или бросает mfa_invalid_code."""
    r = _row(db, user.id)
    secret = crypto.decrypt(r.secret_enc) if r else None
    if r is None or not r.enabled or secret is None:
        raise _err(409, "mfa_not_enabled", "Второй фактор не включён.")
    step = totp.verify(secret, code, r.last_used_step)
    if step is not None:
        r.last_used_step = step
        return "totp"
    rec = db.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_hash == _hash(code),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if rec is not None:
        rec.used_at = utcnow()
        audit.record(
            db,
            action="auth.mfa_recovery_used",
            actor_user_id=user.id,
            object_type="user",
            object_id=user.id,
        )
        return "recovery"
    _fail_attempt(db, user)
    audit.record(
        db,
        action="auth.mfa_failed",
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
        after={"stage": "verify"},
    )
    db.commit()
    raise _err(401, "mfa_invalid_code", "Неверный код.")


def verify_login(db: Session, user: User, code: str) -> None:
    """Вызывается из входа после проверки пароля."""
    _check_second_factor(db, user, code)


def disable(db: Session, principal: Principal, password: str, code: str) -> None:
    user = principal.user
    _throttle(db, user)
    if not verify_password(user.password_hash, password):
        _fail_attempt(db, user)
        db.commit()
        raise _err(422, "invalid_current_password", "Пароль указан неверно.")
    _check_second_factor(db, user, code)
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    db.execute(delete(UserMfa).where(UserMfa.user_id == user.id))
    audit.record(
        db, action="auth.mfa_disabled", actor_user_id=user.id, object_type="user", object_id=user.id
    )
    db.commit()


def regenerate_codes(db: Session, principal: Principal, password: str, code: str) -> list[str]:
    user = principal.user
    _throttle(db, user)
    if not verify_password(user.password_hash, password):
        _fail_attempt(db, user)
        db.commit()
        raise _err(422, "invalid_current_password", "Пароль указан неверно.")
    _check_second_factor(db, user, code)
    codes = _new_recovery_codes(db, user.id)
    audit.record(
        db,
        action="auth.mfa_codes_regenerated",
        actor_user_id=user.id,
        object_type="user",
        object_id=user.id,
    )
    db.commit()
    return codes
