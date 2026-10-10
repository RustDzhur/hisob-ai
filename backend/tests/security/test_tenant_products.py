from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.helpers import csrf_headers, setup_org

NewClient = Callable[[], TestClient]


def _ctx(conn: Any, org: str = "") -> None:
    conn.execute(
        text(
            "SELECT set_config('app.current_org', :o, true), set_config('app.current_user', '', true)"
        ),
        {"o": org},
    )


def _seed(new_client: NewClient) -> tuple[str, str, str]:
    orgs, pids = [], []
    for email in ("a@example.uz", "b@example.uz"):
        c, org = setup_org(new_client, email)
        uid = next(
            u["id"]
            for u in c.get(f"/api/v1/organizations/{org}/units").json()
            if u["code"] == "pcs"
        )
        r = c.post(
            f"/api/v1/organizations/{org}/products",
            json={
                "kind": "goods",
                "sku": "S1",
                "name": "Товар",
                "unit_id": uid,
                "barcodes": ["CODE-0001"],
            },
            headers=csrf_headers(c),
        )
        orgs.append(org)
        pids.append(r.json()["id"])
    return orgs[0], orgs[1], pids[1]


def test_rls_products_and_identifiers(new_client: NewClient, app_engine: Engine) -> None:
    org_a, _, _ = _seed(new_client)
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM product")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM product_identifier")).scalar_one() == 0
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert {r[0] for r in c.execute(text("SELECT organization_id::text FROM product"))} == {
            org_a
        }
        assert {
            r[0] for r in c.execute(text("SELECT organization_id::text FROM product_identifier"))
        } == {org_a}


def test_rls_blocks_foreign_writes(new_client: NewClient, app_engine: Engine) -> None:
    org_a, org_b, pid_b = _seed(new_client)
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert (
            c.execute(
                text("UPDATE product SET name='x' WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_b},
            ).rowcount
            == 0
        )
        assert (
            c.execute(
                text("DELETE FROM product_identifier WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_b},
            ).rowcount
            == 0
        )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO product_identifier (id, organization_id, product_id, kind, value) VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:p AS uuid), 'barcode', 'EVIL-001')"
                ),
                {"o": org_b, "p": pid_b},
            )


def test_composite_fk_blocks_cross_org_identifier(
    new_client: NewClient, admin_engine: Engine
) -> None:
    org_a, _org_b, pid_b = _seed(new_client)
    # даже суперпользователь не может привязать идентификатор организации A к товару организации B
    with admin_engine.begin() as c, pytest.raises(IntegrityError):
        c.execute(
            text(
                "INSERT INTO product_identifier (id, organization_id, product_id, kind, value) VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:p AS uuid), 'barcode', 'MIX-0001')"
            ),
            {"o": org_a, "p": pid_b},
        )


def test_db_constraints_on_price(new_client: NewClient, admin_engine: Engine) -> None:
    org_a, _, _ = _seed(new_client)
    ins = (
        "INSERT INTO product (id, organization_id, kind, sku, name, unit_id, price, price_currency) "
        "SELECT gen_random_uuid(), CAST(:o AS uuid), :k, :s, 'n', id, :p, :c FROM unit_of_measure LIMIT 1"
    )
    for k, s, p, c in (
        ("goods", "C1", 5, None),
        ("goods", "C2", None, "UZS"),
        ("goods", "C3", -1, "UZS"),
        ("bad", "C4", None, None),
    ):
        with admin_engine.begin() as conn, pytest.raises(IntegrityError):
            conn.execute(text(ins), {"o": org_a, "k": k, "s": s, "p": p, "c": c})


def test_app_role_cannot_delete_products(new_client: NewClient, app_engine: Engine) -> None:
    org_a, _, _ = _seed(new_client)
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(text("DELETE FROM product"))
