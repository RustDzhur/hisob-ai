import logging

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.modules.catalog.counterparties_router import router as counterparties_router
from app.modules.catalog.products_router import router as products_router
from app.modules.catalog.router import currencies_router
from app.modules.catalog.router import router as catalog_router
from app.modules.identity.router import router as auth_router
from app.modules.inventory.router import router as warehouses_router
from app.modules.rbac.router import router as roles_router
from app.modules.tenancy.router import invitations_router
from app.modules.tenancy.router import router as org_router
from app.platform.db import get_engine
from app.platform.errors import install_error_handlers
from app.platform.logging import configure_logging
from app.platform.middleware import RequestContextMiddleware


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Hisob AI API",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: процесс жив."""
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"], response_model=None)
    def ready() -> JSONResponse | dict[str, str]:
        """Readiness: БД отвечает."""
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            logging.getLogger("app").exception("readiness check failed")
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_router)
    api.include_router(org_router)
    api.include_router(catalog_router)
    api.include_router(currencies_router)
    api.include_router(counterparties_router)
    api.include_router(products_router)
    api.include_router(warehouses_router)
    api.include_router(invitations_router)
    api.include_router(roles_router)
    app.include_router(api)
    return app


app = create_app()
