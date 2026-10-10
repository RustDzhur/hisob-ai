"""Изоляция организаций: на уровне API и на уровне SQL (RLS)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import create_org, register


def _ctx(conn, user: str = "", org: str = "") -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            "SELECT set_config('app.current_user', :u, true), set_config('app.current_org', :o, true)"
        ),
        {"u": user, "o": org},
    )


# ---------------- API ----------------


def test_foreign_org_is_indistinguishable_from_missing(
    client: TestClient, client2: TestClient
) -> None:
    register(client, "a@example.uz")
    org_a = create_org(client, "Org A").json()
    register(client2, "b@example.uz")

    foreign = client2.get(f"/api/v1/organizations/{org_a['id']}")
    missing = client2.get(f"/api/v1/organizations/{uuid.uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "not_found"
    assert foreign.json()["message"] == missing.json()["message"]


def test_unauthenticated_cannot_read_org(client: TestClient, client2: TestClient) -> None:
    register(client, "a@example.uz")
    org_a = create_org(client, "Org A").json()
    assert client2.get(f"/api/v1/organizations/{org_a['id']}").status_code == 401


def test_org_id_header_or_query_cannot_switch_context(
    client: TestClient, client2: TestClient
) -> None:
    register(client, "a@example.uz")
    org_a = create_org(client, "Org A").json()
    register(client2, "b@example.uz")
    r = client2.get(
        "/api/v1/organizations",
        headers={"X-Organization-Id": org_a["id"]},
        params={"organization_id": org_a["id"]},
    )
    assert r.json() == []


# ---------------- RLS (SQL от имени hisob_app) ----------------


def _seed_two_orgs(
    client: TestClient, client2: TestClient
) -> tuple[dict[str, str], dict[str, str], str, str]:
    ua = register(client, "a@example.uz").json()
    oa = create_org(client, "Org A").json()
    ub = register(client2, "b@example.uz").json()
    ob = create_org(client2, "Org B").json()
    return oa, ob, ua["id"], ub["id"]


def test_app_role_is_not_privileged(app_engine: Engine) -> None:
    with app_engine.connect() as c:
        row = c.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert tuple(row) == (False, False)
        owned = c.execute(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner = current_user"
            )
        ).scalar_one()
        assert owned == 0


def test_rls_no_context_sees_nothing(
    client: TestClient, client2: TestClient, app_engine: Engine
) -> None:
    _seed_two_orgs(client, client2)
    with app_engine.connect() as c:
        for table in ("membership", "audit_event"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table


def test_rls_org_context_sees_only_that_org(
    client: TestClient, client2: TestClient, app_engine: Engine
) -> None:
    oa, ob, _, _ = _seed_two_orgs(client, client2)
    with app_engine.connect() as c:
        _ctx(c, org=oa["id"])
        orgs = {r[0] for r in c.execute(text("SELECT organization_id::text FROM membership"))}
        assert orgs == {oa["id"]}
        audit_orgs = {
            r[0] for r in c.execute(text("SELECT DISTINCT organization_id::text FROM audit_event"))
        }
        assert audit_orgs == {oa["id"]}
        c.rollback()
        _ctx(c, org=ob["id"])
        assert {r[0] for r in c.execute(text("SELECT organization_id::text FROM membership"))} == {
            ob["id"]
        }


def test_rls_user_context_sees_only_own_memberships(
    client: TestClient, client2: TestClient, app_engine: Engine
) -> None:
    oa, _, ua, _ = _seed_two_orgs(client, client2)
    with app_engine.connect() as c:
        _ctx(c, user=ua)
        rows = [r[0] for r in c.execute(text("SELECT organization_id::text FROM membership"))]
        assert rows == [oa["id"]]


def test_rls_blocks_writes_into_foreign_org(
    client: TestClient, client2: TestClient, app_engine: Engine
) -> None:
    oa, ob, _, ub = _seed_two_orgs(client, client2)
    with app_engine.connect() as c:
        _ctx(c, user=ub, org=oa["id"])  # b пытается писать в org A, не будучи в ней
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO membership (id, organization_id, user_id, role_id, status) "
                    "SELECT gen_random_uuid(), :org, :usr, id, 'active' FROM role LIMIT 1"
                ),
                {"org": ob["id"], "usr": ub},  # org B при контексте org A
            )
    with app_engine.connect() as c:
        _ctx(c, org=oa["id"])
        n = c.execute(
            text("UPDATE membership SET status='suspended' WHERE organization_id = :o"),
            {"o": ob["id"]},
        ).rowcount
        assert n == 0  # чужие строки не видны для UPDATE


def test_rls_context_does_not_leak_between_pooled_connections(
    client: TestClient, client2: TestClient, app_engine: Engine
) -> None:
    oa, _, _, _ = _seed_two_orgs(client, client2)
    # Один физический коннект переиспользуется пулом (pool_size > 0, последовательно).
    with app_engine.connect() as c:
        pid1 = c.execute(text("SELECT pg_backend_pid()")).scalar_one()
        _ctx(c, org=oa["id"])
        assert c.execute(text("SELECT count(*) FROM membership")).scalar_one() == 1
        c.commit()
    with app_engine.connect() as c:
        pid2 = c.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert pid1 == pid2  # тот же коннект
        assert c.execute(text("SELECT count(*) FROM membership")).scalar_one() == 0


# ---------------- Аудит неизменяем ----------------


def test_audit_is_append_only_for_app_role(
    client: TestClient, client2: TestClient, app_engine: Engine, admin_engine: Engine
) -> None:
    _seed_two_orgs(client, client2)
    with app_engine.connect() as c:
        with pytest.raises(DBAPIError):
            c.execute(text("UPDATE audit_event SET action='x'"))
    with app_engine.connect() as c:
        with pytest.raises(DBAPIError):
            c.execute(text("DELETE FROM audit_event"))


def test_audit_trigger_blocks_even_superuser(client: TestClient, admin_engine: Engine) -> None:
    register(client, "a@example.uz")
    with admin_engine.connect() as c:
        with pytest.raises(DBAPIError, match="append-only"):
            c.execute(text("UPDATE audit_event SET action='x'"))
        with pytest.raises(DBAPIError, match="append-only"):
            c.execute(text("DELETE FROM audit_event"))
