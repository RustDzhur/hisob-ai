"""Use cases: валюты организации. Курсов и пересчёта здесь нет (решение: после правил бухгалтера)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.catalog import products
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.modules.tenancy.models import Currency, OrganizationCurrency
from app.modules.tenancy.schemas import CurrencyOut
from app.platform.errors import AppError


def _list(db: Session, org_id: uuid.UUID) -> list[CurrencyOut]:
    rows = db.execute(
        select(Currency, OrganizationCurrency)
        .outerjoin(
            OrganizationCurrency,
            (OrganizationCurrency.currency_code == Currency.code)
            & (OrganizationCurrency.organization_id == org_id),
        )
        .order_by(Currency.code)
    ).all()
    out = [
        CurrencyOut(
            code=c.code,
            minor_units=c.minor_units,
            name_uz=c.name_uz,
            name_ru=c.name_ru,
            name_en=c.name_en,
            enabled=bool(oc and oc.enabled),
            is_base=bool(oc and oc.is_base),
        )
        for c, oc in rows
    ]
    return sorted(out, key=lambda x: (not x.is_base, not x.enabled, x.code))


def list_currencies(db: Session, principal: Principal, org_id: uuid.UUID) -> list[CurrencyOut]:
    authorize(db, principal, org_id, rbac.CATALOG_READ)
    return _list(db, org_id)


def set_enabled(
    db: Session, principal: Principal, org_id: uuid.UUID, code: str, enabled: bool
) -> list[CurrencyOut]:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    code = code.upper()
    if db.get(Currency, code) is None:
        raise AppError(404, "not_found", "Валюта не найдена.")
    row = db.scalar(
        select(OrganizationCurrency)
        .where(
            OrganizationCurrency.organization_id == org_id,
            OrganizationCurrency.currency_code == code,
        )
        .with_for_update()
    )
    if row is not None and row.is_base and not enabled:
        raise AppError(409, "base_currency_locked", "Базовую валюту отключить нельзя.")
    if not enabled and row is not None and row.enabled:
        in_use = products.count_active_by_currency(db, org_id, code)
        if in_use:
            raise AppError(
                409,
                "currency_in_use",
                "Валюта используется в ценах товаров.",
                [
                    {
                        "field": "code",
                        "code": "currency_in_use",
                        "message": "Валюта используется.",
                        "params": {"count": in_use},
                    }
                ],
            )
    was_enabled = bool(row and row.enabled)
    if was_enabled == enabled:
        return _list(db, org_id)  # без изменений — без записи в аудит
    if row is None:
        db.add(OrganizationCurrency(organization_id=org_id, currency_code=code, enabled=enabled))
    else:
        row.enabled = enabled
    audit.record(
        db,
        action="currency.enabled" if enabled else "currency.disabled",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="currency",
        after={"code": code},
    )
    db.commit()
    return _list(db, org_id)
