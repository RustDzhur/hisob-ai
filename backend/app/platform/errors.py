import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.platform.context import correlation_id


class AppError(Exception):
    """Ожидаемая бизнес-ошибка с устойчивым кодом."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers or {}


def error_body(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "type": f"https://hisob.ai/errors/{code}",
        "code": code,
        "message": message,
        "details": details or [],
        "correlation_id": correlation_id.get(),
    }


_PYDANTIC_CODES = {
    "missing": "required",
    "string_too_short": "too_short",
    "string_too_long": "too_long",
    "string_pattern_mismatch": "invalid",
}


def _detail_code(err: Any) -> str:
    """Устойчивый код ошибки поля для локализации на клиенте."""
    if err["type"] == "value_error":
        raised = str(err.get("ctx", {}).get("error", ""))
        if re.fullmatch(r"[a-z][a-z0-9_]{2,40}", raised):
            return raised  # наши валидаторы бросают ValueError("tin_invalid") и т.п.
        return "invalid"
    return _PYDANTIC_CODES.get(str(err["type"]), "invalid")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details),
            status_code=exc.status,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(p) for p in e["loc"] if p != "body"),
                "code": _detail_code(e),
                "message": e["msg"],
            }
            for e in exc.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "Проверьте введённые данные.", details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code)
