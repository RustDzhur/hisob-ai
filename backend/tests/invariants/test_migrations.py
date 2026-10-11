from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from app import registry  # noqa: F401  (все модели: иначе сравнение зависит от порядка импортов в сессии)
from app.platform.base import Base


def test_models_match_migrations(admin_engine: Engine) -> None:
    """Схема после миграций совпадает с моделями SQLAlchemy (нет скрытого дрейфа)."""
    with admin_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_seeded_roles_and_permissions(admin_engine: Engine) -> None:
    from sqlalchemy import text

    with admin_engine.connect() as c:
        roles = {r[0] for r in c.execute(text("SELECT code FROM role"))}
        assert roles == {
            "owner",
            "org_admin",
            "accountant",
            "storekeeper",
            "sales_purchase_manager",
            "document_operator",
            "auditor",
        }
        auditor = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT permission_code FROM role_permission rp JOIN role r ON r.id=rp.role_id WHERE r.code='auditor'"
                )
            )
        }
        assert auditor == {
            "accounting.read",
            "bank.read",
            "org.read",
            "members.read",
            "audit.read",
            "catalog.read",
            "copilot.use",
            "documents.read",
            "integrations.read",
            "stock.read",
            "warehouses.read",
        }
