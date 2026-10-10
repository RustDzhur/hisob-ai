from alembic import context
from sqlalchemy import create_engine

from app.modules.audit import models as _audit  # noqa: F401
from app.modules.catalog import models as _catalog  # noqa: F401
from app.modules.identity import models as _identity  # noqa: F401
from app.modules.inventory import models as _inventory  # noqa: F401
from app.modules.rbac import models as _rbac  # noqa: F401
from app.modules.tenancy import models as _tenancy  # noqa: F401
from app.platform import idempotency as _idem  # noqa: F401
from app.platform.base import Base
from app.platform.config import get_settings

settings = get_settings()
url = settings.migration_database_url or settings.database_url
target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
