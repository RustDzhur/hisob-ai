"""Use cases: единицы измерения (системные неизменяемы, свои создаёт организация)."""

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.catalog import products
from app.modules.catalog.models import UnitOfMeasure
from app.modules.catalog.schemas import UnitCreate, UnitOut, UnitUpdate
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id
from app.platform.errors import AppError


def _out(u: UnitOfMeasure) -> UnitOut:
    return UnitOut(
        id=u.id,
        code=u.code,
        name_uz=u.name_uz,
        name_ru=u.name_ru,
        name_en=u.name_en,
        is_system=u.organization_id is None,
        is_active=u.is_active,
    )


def _snapshot(u: UnitOfMeasure) -> dict[str, Any]:
    return {
        "code": u.code,
        "name_uz": u.name_uz,
        "name_ru": u.name_ru,
        "name_en": u.name_en,
        "is_active": u.is_active,
    }


def list_units(db: Session, principal: Principal, org_id: uuid.UUID) -> list[UnitOut]:
    authorize(db, principal, org_id, rbac.CATALOG_READ)
    rows = db.scalars(
        select(UnitOfMeasure)
        .where(
            or_(UnitOfMeasure.organization_id.is_(None), UnitOfMeasure.organization_id == org_id)
        )
        .order_by(UnitOfMeasure.organization_id.is_not(None), UnitOfMeasure.code)
    )
    return [_out(u) for u in rows]


def _code_taken() -> AppError:
    return AppError(
        409,
        "unit_code_taken",
        "Единица с таким кодом уже существует.",
        [{"field": "code", "code": "unit_code_taken", "message": "Код уже используется."}],
    )


def create_unit(db: Session, principal: Principal, org_id: uuid.UUID, data: UnitCreate) -> UnitOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    # Код уникален среди системных и своих единиц (регистр не важен).
    clash = db.scalar(
        select(UnitOfMeasure.id).where(
            UnitOfMeasure.code == data.code,
            or_(UnitOfMeasure.organization_id.is_(None), UnitOfMeasure.organization_id == org_id),
        )
    )
    if clash is not None:
        raise _code_taken()
    unit = UnitOfMeasure(
        id=new_id(),
        organization_id=org_id,
        code=data.code,
        name_uz=data.name_uz,
        name_ru=data.name_ru,
        name_en=data.name_en,
    )
    db.add(unit)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _code_taken() from exc
    audit.record(
        db,
        action="unit.created",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="unit_of_measure",
        object_id=unit.id,
        after=_snapshot(unit),
    )
    db.commit()
    return _out(unit)


def update_unit(
    db: Session, principal: Principal, org_id: uuid.UUID, unit_id: uuid.UUID, data: UnitUpdate
) -> UnitOut:
    authorize(db, principal, org_id, rbac.CATALOG_MANAGE)
    unit = db.scalar(
        select(UnitOfMeasure).where(
            UnitOfMeasure.id == unit_id,
            or_(UnitOfMeasure.organization_id.is_(None), UnitOfMeasure.organization_id == org_id),
        )
    )
    if unit is None:
        raise AppError(404, "not_found", "Единица измерения не найдена.")
    if unit.organization_id is None:
        raise AppError(409, "system_unit_readonly", "Системную единицу изменить нельзя.")
    # Блокировка только для своей строки: SELECT ... FOR UPDATE подчиняется политике UPDATE.
    db.refresh(unit, with_for_update=True)

    if data.is_active is False and unit.is_active:
        in_use = products.count_active_by_unit(db, org_id, unit.id)
        if in_use:
            raise AppError(
                409,
                "unit_in_use",
                "Единица используется в товарах.",
                [
                    {
                        "field": "is_active",
                        "code": "unit_in_use",
                        "message": "Единица используется.",
                        "params": {"count": in_use},
                    }
                ],
            )
    before = _snapshot(unit)
    for field in data.model_fields_set:
        value = getattr(data, field)
        if field == "is_active":
            if value is not None:
                unit.is_active = value
        else:
            setattr(unit, field, value)
    if not (unit.name_uz or unit.name_ru or unit.name_en):
        db.rollback()
        raise AppError(
            422,
            "validation_error",
            "Нужно название хотя бы на одном языке.",
            [
                {
                    "field": "name_uz",
                    "code": "unit_name_required",
                    "message": "Нужно хотя бы одно название.",
                }
            ],
        )
    after = _snapshot(unit)
    if after != before:
        audit.record(
            db,
            action="unit.updated",
            actor_user_id=principal.user.id,
            organization_id=org_id,
            object_type="unit_of_measure",
            object_id=unit.id,
            before=before,
            after=after,
        )
    db.commit()
    return _out(unit)
