import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.platform import context
from app.platform.config import get_settings
from app.platform.errors import error_body

log = logging.getLogger("app.access")
_CID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def _client_ip(request: Request) -> str | None:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-correlation-id", "")
        cid = incoming if _CID_RE.match(incoming) else uuid.uuid4().hex
        context.correlation_id.set(cid)
        context.client_ip.set(_client_ip(request))
        context.user_agent.set((request.headers.get("user-agent") or "")[:300])
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"fields": {"path": request.url.path}})
            response = JSONResponse(
                error_body(
                    "internal_error", "Внутренняя ошибка. Укажите correlation_id в обращении."
                ),
                status_code=500,
            )
        response.headers["X-Correlation-ID"] = cid
        log.info(
            "request",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            },
        )
        return response
