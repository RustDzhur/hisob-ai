import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import PASSWORD, csrf_headers, register


def audit_actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


def test_register_sets_cookies_and_me_works(client: TestClient, admin_engine: Engine) -> None:
    r = register(client, "Owner@Example.uz")
    assert r.status_code == 201
    assert r.json()["email"] == "owner@example.uz"
    assert "password" not in json.dumps(r.json())

    set_cookie = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("hisob_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    csrf_cookie = next(c for c in set_cookie if c.startswith("hisob_csrf="))
    assert "HttpOnly" not in csrf_cookie

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "owner@example.uz"
    assert audit_actions(admin_engine) == ["auth.registered", "auth.login"]


def test_me_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "not_authenticated" and body["correlation_id"]


def test_duplicate_email_conflict_case_insensitive(client: TestClient, client2: TestClient) -> None:
    assert register(client, "a@example.uz").status_code == 201
    r = register(client2, "A@EXAMPLE.UZ")
    assert r.status_code == 409 and r.json()["code"] == "email_taken"


def test_weak_password_and_bad_email_rejected(client: TestClient) -> None:
    r = register(client, "a@example.uz", password="short")
    assert r.status_code == 422 and r.json()["code"] == "weak_password"
    r = register(client, "not-an-email")
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


def test_login_logout_and_token_reuse_rejected(client: TestClient, client2: TestClient) -> None:
    register(client, "u@example.uz")
    bad = client2.post(
        "/api/v1/auth/login", json={"email": "u@example.uz", "password": "wrong-pass-123"}
    )
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_credentials"
    unknown = client2.post(
        "/api/v1/auth/login", json={"email": "nobody@example.uz", "password": PASSWORD}
    )
    assert unknown.status_code == 401 and unknown.json()["message"] == bad.json()["message"]

    ok = client2.post("/api/v1/auth/login", json={"email": "U@example.uz", "password": PASSWORD})
    assert ok.status_code == 200
    stolen = client2.cookies.get("hisob_session")
    assert stolen

    # logout без CSRF-заголовка запрещён
    assert client2.post("/api/v1/auth/logout").status_code == 403
    out = client2.post("/api/v1/auth/logout", headers=csrf_headers(client2))
    assert out.status_code == 204

    # старый токен после выхода не работает
    client2.cookies.set("hisob_session", stolen)
    assert client2.get("/api/v1/auth/me").status_code == 401


def test_login_throttle_after_repeated_failures(client: TestClient, client2: TestClient) -> None:
    register(client, "t@example.uz")
    codes = [
        client2.post(
            "/api/v1/auth/login", json={"email": "t@example.uz", "password": "bad-password-1"}
        ).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    # даже верный пароль во время блокировки не принимается
    ok = client2.post("/api/v1/auth/login", json={"email": "t@example.uz", "password": PASSWORD})
    assert ok.status_code == 429 and "retry-after" in ok.headers


def test_audit_records_login_and_failed_login(
    client: TestClient, client2: TestClient, admin_engine: Engine
) -> None:
    register(client, "au@example.uz")
    client2.post(
        "/api/v1/auth/login", json={"email": "au@example.uz", "password": "wrong-pass-123"}
    )
    client2.post("/api/v1/auth/login", json={"email": "au@example.uz", "password": PASSWORD})
    actions = audit_actions(admin_engine)
    assert actions.count("auth.login_failed") == 1
    assert actions.count("auth.login") == 2


def test_no_secrets_in_audit_db_or_logs(
    client: TestClient, admin_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "ultra-secret-pass-987"
    with caplog.at_level(logging.DEBUG):
        register(client, "sec@example.uz", password=secret)
        client.post("/api/v1/auth/login", json={"email": "sec@example.uz", "password": secret})
    token = client.cookies.get("hisob_session") or ""
    with admin_engine.connect() as c:
        dump = json.dumps([list(map(str, r)) for r in c.execute(text("SELECT * FROM audit_event"))])
        stored = c.execute(
            text("SELECT token_hash, password_hash FROM sessions, users LIMIT 1")
        ).one()
    assert secret not in dump and token not in dump
    assert secret not in caplog.text and token not in caplog.text
    # в БД лежит только хэш токена и argon2-хэш пароля
    assert token.encode() not in bytes(stored[0]) and stored[1].startswith("$argon2id$")


def test_correlation_id_echoed_and_generated(client: TestClient) -> None:
    r = client.get("/health", headers={"X-Correlation-ID": "abc-12345678"})
    assert r.headers["x-correlation-id"] == "abc-12345678"
    r2 = client.get("/health", headers={"X-Correlation-ID": "bad id!"})
    assert r2.headers["x-correlation-id"] != "bad id!"


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
