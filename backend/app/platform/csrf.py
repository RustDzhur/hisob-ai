import hmac

from fastapi import Request

from app.platform.errors import AppError

CSRF_COOKIE = "hisob_csrf"
CSRF_HEADER = "X-CSRF-Token"


def require_csrf(request: Request) -> None:
    """Double-submit: значение cookie должно совпасть с заголовком."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not hmac.compare_digest(cookie, header):
        raise AppError(403, "csrf_failed", "Сессия формы устарела. Обновите страницу и повторите.")
