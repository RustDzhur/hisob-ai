import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import create_org, csrf_headers, register


def test_create_org_makes_creator_owner_and_audits(
    client: TestClient, admin_engine: Engine
) -> None:
    register(client, "o@example.uz")
    r = create_org(client, "ООО Ромашка", tin="123456789")
    assert r.status_code == 201
    body = r.json()
    assert (
        body["role"] == "owner"
        and body["timezone"] == "Asia/Tashkent"
        and body["base_currency"] == "UZS"
    )
    assert set(body["permissions"]) >= {"org.read", "org.manage", "members.manage", "audit.read"}

    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM membership")).scalar_one() == 1
        row = c.execute(
            text("SELECT action, organization_id::text FROM audit_event WHERE action='org.created'")
        ).one()
        assert row[1] == body["id"]

    listed = client.get("/api/v1/organizations").json()
    assert [o["id"] for o in listed] == [body["id"]]
    assert client.get(f"/api/v1/organizations/{body['id']}").status_code == 200


def test_create_org_requires_auth_csrf_and_idempotency_key(
    client: TestClient, client2: TestClient
) -> None:
    assert client2.post("/api/v1/organizations", json={"name": "X org"}).status_code == 401
    register(client, "c@example.uz")
    no_csrf = client.post(
        "/api/v1/organizations", json={"name": "X org"}, headers={"Idempotency-Key": "k" * 12}
    )
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "csrf_failed"
    no_key = client.post(
        "/api/v1/organizations", json={"name": "X org"}, headers=csrf_headers(client)
    )
    assert no_key.status_code == 400 and no_key.json()["code"] == "idempotency_key_required"


def test_create_org_validation(client: TestClient) -> None:
    register(client, "v@example.uz")
    for bad in (
        {"name": "A"},
        {"name": "Ok name", "tin": "12ab"},
        {"name": "Ok name", "timezone": "Mars/Base"},
        {"name": "Ok name", "base_currency": "uzs1"},
    ):
        r = create_org(client, **bad)
        assert r.status_code == 422, bad
        assert r.json()["code"] == "validation_error"


def test_idempotent_replay_returns_same_org(client: TestClient, admin_engine: Engine) -> None:
    register(client, "i@example.uz")
    key = uuid.uuid4().hex
    h = csrf_headers(client, key)
    first = client.post("/api/v1/organizations", json={"name": "ООО Один"}, headers=h)
    second = client.post("/api/v1/organizations", json={"name": "ООО Один"}, headers=h)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["idempotent-replayed"] == "true"
    other = client.post("/api/v1/organizations", json={"name": "ООО Другое"}, headers=h)
    assert other.status_code == 422 and other.json()["code"] == "idempotency_key_reuse"
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM organization")).scalar_one() == 1
        assert (
            c.execute(
                text("SELECT count(*) FROM audit_event WHERE action='org.created'")
            ).scalar_one()
            == 1
        )


def test_concurrent_same_key_creates_exactly_one_org(
    client: TestClient, admin_engine: Engine
) -> None:
    register(client, "race@example.uz")
    key = uuid.uuid4().hex
    headers = csrf_headers(client, key)
    cookies = dict(client.cookies)

    def call(_: int) -> int:
        from app.main import app

        with TestClient(app, cookies=cookies) as c:
            resp = c.post("/api/v1/organizations", json={"name": "ООО Гонка"}, headers=headers)
            return int(resp.status_code)

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(call, range(4)))
    assert set(codes) <= {201, 409}, codes
    assert codes.count(201) >= 1
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM organization")).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM membership")).scalar_one() == 1


def test_orgs_listing_only_shows_own(client: TestClient, client2: TestClient) -> None:
    register(client, "a@example.uz")
    org_a = create_org(client, "Org A").json()
    register(client2, "b@example.uz")
    assert client2.get("/api/v1/organizations").json() == []
    org_b = create_org(client2, "Org B").json()
    assert [o["id"] for o in client.get("/api/v1/organizations").json()] == [org_a["id"]]
    assert [o["id"] for o in client2.get("/api/v1/organizations").json()] == [org_b["id"]]
