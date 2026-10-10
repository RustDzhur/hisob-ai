import json
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import PASSWORD, csrf_headers, login, register

NewClient = Callable[[], TestClient]
NEW_PASSWORD = "another strong pass 42"


def change(client: TestClient, current: str, new: str) -> object:
    return client.post(
        "/api/v1/auth/change-password",
        json={"current_password": current, "new_password": new},
        headers=csrf_headers(client),
    )


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


def test_change_password_revokes_other_sessions_keeps_current(
    new_client: NewClient, admin_engine: Engine
) -> None:
    a = new_client()
    register(a, "u@example.uz")
    b = new_client()
    assert login(b, "u@example.uz").status_code == 200

    r = change(a, PASSWORD, NEW_PASSWORD)
    assert r.status_code == 204  # type: ignore[attr-defined]
    assert a.get("/api/v1/auth/me").status_code == 200  # текущая сессия жива
    assert b.get("/api/v1/auth/me").status_code == 401  # остальные завершены

    c = new_client()
    assert login(c, "u@example.uz", PASSWORD).status_code == 401
    assert login(c, "u@example.uz", NEW_PASSWORD).status_code == 200
    with admin_engine.connect() as conn:
        row = conn.execute(
            text("SELECT after FROM audit_event WHERE action='auth.password_changed'")
        ).scalar_one()
        dump = json.dumps(
            [list(map(str, r)) for r in conn.execute(text("SELECT * FROM audit_event"))]
        )
    assert row == {"revoked_other_sessions": 1}
    assert PASSWORD not in dump and NEW_PASSWORD not in dump


def test_change_password_validation_and_auth(new_client: NewClient, admin_engine: Engine) -> None:
    anon = new_client()
    assert (
        anon.post(
            "/api/v1/auth/change-password", json={"current_password": "x", "new_password": "y"}
        ).status_code
        == 401
    )
    a = new_client()
    register(a, "v@example.uz")
    no_csrf = a.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert no_csrf.status_code == 403

    wrong = change(a, "not-my-password", NEW_PASSWORD)
    assert wrong.status_code == 422 and wrong.json()["code"] == "invalid_current_password"  # type: ignore[attr-defined]
    weak = change(a, PASSWORD, "short")
    assert weak.status_code == 422 and weak.json()["code"] == "weak_password"  # type: ignore[attr-defined]
    same = change(a, PASSWORD, PASSWORD)
    assert same.status_code == 422 and same.json()["code"] == "weak_password"  # type: ignore[attr-defined]
    assert login(new_client(), "v@example.uz", PASSWORD).status_code == 200  # пароль не изменился
    assert "auth.password_change_failed" in actions(admin_engine)


def test_change_password_attempts_are_throttled(new_client: NewClient) -> None:
    a = new_client()
    register(a, "t@example.uz")
    codes = [change(a, "bad-password-12", NEW_PASSWORD).status_code for _ in range(6)]  # type: ignore[attr-defined]
    assert codes[:5] == [422] * 5 and codes[5] == 429
    assert change(a, PASSWORD, NEW_PASSWORD).status_code == 429  # type: ignore[attr-defined]


def test_sessions_list_shows_only_own_and_marks_current(new_client: NewClient) -> None:
    a1 = new_client()
    register(a1, "a@example.uz")
    a2 = new_client()
    login(a2, "a@example.uz")
    other = new_client()
    register(other, "b@example.uz")

    sessions = a1.get("/api/v1/auth/sessions").json()
    assert len(sessions) == 2 and [s["current"] for s in sessions].count(True) == 1
    assert len(other.get("/api/v1/auth/sessions").json()) == 1
    assert all("token" not in json.dumps(s) for s in sessions)
    assert new_client().get("/api/v1/auth/sessions").status_code == 401


def test_revoke_single_session_and_foreign_session_is_404(
    new_client: NewClient, admin_engine: Engine
) -> None:
    a1 = new_client()
    register(a1, "a@example.uz")
    a2 = new_client()
    login(a2, "a@example.uz")
    other = new_client()
    register(other, "b@example.uz")

    ids = {s["id"]: s["current"] for s in a1.get("/api/v1/auth/sessions").json()}
    other_id = next(iter(c for c, cur in ids.items() if not cur))
    foreign = other.delete(f"/api/v1/auth/sessions/{other_id}", headers=csrf_headers(other))
    assert foreign.status_code == 404
    assert a2.get("/api/v1/auth/me").status_code == 200

    assert (
        a1.delete(f"/api/v1/auth/sessions/{other_id}", headers=csrf_headers(a1)).status_code == 204
    )
    assert a2.get("/api/v1/auth/me").status_code == 401
    assert a1.get("/api/v1/auth/me").status_code == 200
    assert (
        a1.delete(f"/api/v1/auth/sessions/{other_id}", headers=csrf_headers(a1)).status_code == 404
    )
    assert "auth.session_revoked" in actions(admin_engine)


def test_logout_all_ends_every_session(new_client: NewClient, admin_engine: Engine) -> None:
    a1 = new_client()
    register(a1, "a@example.uz")
    a2 = new_client()
    login(a2, "a@example.uz")
    assert a1.post("/api/v1/auth/logout-all").status_code == 403  # без CSRF
    assert a1.post("/api/v1/auth/logout-all", headers=csrf_headers(a1)).status_code == 204
    assert a1.get("/api/v1/auth/me").status_code == 401
    assert a2.get("/api/v1/auth/me").status_code == 401
    assert "auth.logout_all" in actions(admin_engine)
    assert login(new_client(), "a@example.uz").status_code == 200  # войти снова можно
