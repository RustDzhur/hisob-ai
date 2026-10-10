"""RLS для справочников: валюты организации, единицы измерения."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import csrf_headers, setup_org

NewClient = Callable[[], TestClient]


def _ctx(conn: Any, org: str = "") -> None:
    conn.execute(
        text(
            "SELECT set_config('app.current_org', :o, true), set_config('app.current_user', '', true)"
        ),
        {"o": org},
    )


def _two(new_client: NewClient) -> tuple[TestClient, str, TestClient, str]:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    for c, org in ((a, org_a), (b, org_b)):
        c.post(
            f"/api/v1/organizations/{org}/units",
            json={"code": "box", "name_en": f"box of {org[:4]}"},
            headers=csrf_headers(c),
        )
    return a, org_a, b, org_b


def test_rls_units_no_context_sees_only_system(new_client: NewClient, app_engine: Engine) -> None:
    _two(new_client)
    with app_engine.connect() as c:
        rows = c.execute(text("SELECT organization_id FROM unit_of_measure")).all()
    assert len(rows) == 5 and all(r[0] is None for r in rows)


def test_rls_units_org_context(new_client: NewClient, app_engine: Engine) -> None:
    _, org_a, _, _org_b = _two(new_client)
    with app_engine.connect() as c:
        _ctx(c, org_a)
        orgs = {str(r[0]) for r in c.execute(text("SELECT organization_id FROM unit_of_measure"))}
    assert orgs == {"None", org_a}


def test_rls_units_cannot_write_foreign_or_system(
    new_client: NewClient, app_engine: Engine
) -> None:
    _, org_a, _, org_b = _two(new_client)
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert (
            c.execute(
                text("UPDATE unit_of_measure SET is_active=false WHERE organization_id IS NULL")
            ).rowcount
            == 0
        )
        assert (
            c.execute(
                text(
                    "UPDATE unit_of_measure SET is_active=false WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org_b},
            ).rowcount
            == 0
        )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO unit_of_measure (id, organization_id, code, name_en) VALUES (gen_random_uuid(), CAST(:o AS uuid), 'zz', 'x')"
                ),
                {"o": org_b},
            )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(
            DBAPIError
        ):  # системные единицы (organization_id NULL) app-роль создавать не может
            c.execute(
                text(
                    "INSERT INTO unit_of_measure (id, code, name_en) VALUES (gen_random_uuid(), 'sys', 'x')"
                )
            )


def test_rls_organization_currency_isolated(new_client: NewClient, app_engine: Engine) -> None:
    _, org_a, _, org_b = _two(new_client)
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM organization_currency")).scalar_one() == 0
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert {
            str(r[0]) for r in c.execute(text("SELECT organization_id FROM organization_currency"))
        } == {org_a}
        assert (
            c.execute(
                text(
                    "UPDATE organization_currency SET enabled=false WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org_b},
            ).rowcount
            == 0
        )


def test_app_role_cannot_modify_global_currency_table(app_engine: Engine) -> None:
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM currency")).scalar_one() == 6
        with pytest.raises(DBAPIError):
            c.execute(text("UPDATE currency SET minor_units = 3"))
