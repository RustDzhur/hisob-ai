"""Регистрация ВСЕХ моделей SQLAlchemy.

API подтягивает модели через роутеры; отдельные процессы (воркер) — нет, и тогда внешние ключи на
незагруженные таблицы не разрешаются. Любой процесс, работающий с БД, импортирует этот модуль.
"""

from app.modules.accounting import models as _accounting
from app.modules.audit import models as _audit
from app.modules.bank import models as _bank
from app.modules.catalog import models as _catalog
from app.modules.documents import models as _documents
from app.modules.identity import models as _identity
from app.modules.inventory import models as _inventory
from app.modules.rbac import models as _rbac
from app.modules.tenancy import models as _tenancy
from app.platform import idempotency as _idempotency

ALL_MODELS = (
    _bank,
    _accounting,
    _audit,
    _catalog,
    _documents,
    _identity,
    _inventory,
    _rbac,
    _tenancy,
    _idempotency,
)
