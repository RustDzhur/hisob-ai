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


def _seed(new_client: NewClient) -> tuple[str, str]:
    orgs = []
    for email in ("a@example.uz", "b@example.uz"):
        c, org = setup_org(new_client, email)
        c.post(
            f"/api/v1/organizations/{org}/warehouses",
            json={"name": "Склад"},
            headers=csrf_headers(c),
        )
        orgs.append(org)
    return orgs[0], orgs[1]


def test_rls_warehouse(new_client: NewClient, app_engine: Engine) -> None:
    org_a, org_b = _seed(new_client)
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM warehouse")).scalar_one() == 0
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert [r[0] for r in c.execute(text("SELECT organization_id::text FROM warehouse"))] == [
            org_a
        ]
        assert (
            c.execute(
                text("UPDATE warehouse SET name='x' WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_b},
            ).rowcount
            == 0
        )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO warehouse (id, organization_id, name) VALUES (gen_random_uuid(), CAST(:o AS uuid), 'Чужой')"
                ),
                {"o": org_b},
            )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(text("DELETE FROM warehouse"))
