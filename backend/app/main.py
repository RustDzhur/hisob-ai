import hmac
import logging

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from app.modules.accounting.router import router as accounting_router
from app.modules.bank.router import router as bank_router
from app.modules.catalog.counterparties_router import router as counterparties_router
from app.modules.catalog.products_router import router as products_router
from app.modules.catalog.router import currencies_router
from app.modules.catalog.router import router as catalog_router
from app.modules.copilot.router import router as copilot_router
from app.modules.documents.router import router as documents_router
from app.modules.identity.router import router as auth_router
from app.modules.integrations.router import router as integrations_router
from app.modules.inventory.router import router as warehouses_router
from app.modules.inventory.stock_extra_router import router as stock_extra_router
from app.modules.inventory.stock_router import router as stock_router
from app.modules.rbac.router import router as roles_router
from app.modules.tenancy.router import invitations_router
from app.modules.tenancy.router import router as org_router
from app.platform import metrics
from app.platform.config import get_settings
from app.platform.db import get_engine
from app.platform.errors import install_error_handlers
from app.platform.logging import configure_logging
from app.platform.middleware import RequestContextMiddleware
from app.platform.ratelimit import RateLimitMiddleware


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Hisob AI API",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.add_middleware(
        RateLimitMiddleware
    )  # внутри RequestContextMiddleware: 429 получают correlation id
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: процесс жив."""
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"], response_model=None)
    def ready() -> JSONResponse | dict[str, str]:
        """Readiness: БД отвечает (и Redis, если очередь настроена)."""
        log = logging.getLogger("app")
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            log.exception("readiness check failed: database")
            return JSONResponse({"status": "unavailable", "failed": "database"}, status_code=503)
        url = get_settings().redis_url
        if url:
            try:
                import redis

                redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1).ping()
            except Exception:
                log.exception("readiness check failed: redis")
                return JSONResponse({"status": "unavailable", "failed": "redis"}, status_code=503)
        return {"status": "ready"}

    @app.get("/metrics", tags=["ops"], include_in_schema=False, response_model=None)
    def metrics_endpoint(request: Request) -> Response:
        """Метрики Prometheus; только с Authorization: Bearer METRICS_TOKEN (без токена в настройках — 404)."""
        token = get_settings().metrics_token
        if not token:
            return PlainTextResponse("not found", status_code=404)
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            return PlainTextResponse("unauthorized", status_code=401)
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_router)
    api.include_router(org_router)
    api.include_router(catalog_router)
    api.include_router(currencies_router)
    api.include_router(counterparties_router)
    api.include_router(products_router)
    api.include_router(warehouses_router)
    api.include_router(stock_router)
    api.include_router(stock_extra_router)
    api.include_router(documents_router)
    api.include_router(accounting_router)
    api.include_router(bank_router)
    api.include_router(copilot_router)
    api.include_router(integrations_router)
    api.include_router(invitations_router)
    api.include_router(roles_router)
    app.include_router(api)
    return app


app = create_app()
