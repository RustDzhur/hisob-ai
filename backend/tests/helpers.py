import uuid
from typing import Any

from fastapi.testclient import TestClient

PASSWORD = "correct horse battery"


def csrf_headers(client: TestClient, key: str | None = None) -> dict[str, str]:
    headers = {"X-CSRF-Token": client.cookies.get("hisob_csrf") or ""}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def register(
    client: TestClient, email: str, password: str = PASSWORD, name: str = "Test User"
) -> Any:
    return client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "full_name": name}
    )


def create_org(client: TestClient, name: str = "ООО Тест", **extra: Any) -> Any:
    return client.post(
        "/api/v1/organizations",
        json={"name": name, **extra},
        headers=csrf_headers(client, uuid.uuid4().hex),
    )


def login(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def invite(client: TestClient, org_id: str, email: str, role: str) -> Any:
    return client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        json={"email": email, "role_code": role},
        headers=csrf_headers(client),
    )


def accept(client: TestClient, token: str) -> Any:
    return client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=csrf_headers(client)
    )


def setup_org(new_client: Any, email: str = "owner@example.uz") -> tuple[TestClient, str]:
    owner = new_client()
    register(owner, email)
    org_id: str = create_org(owner, "ООО Владелец").json()["id"]
    return owner, org_id


def join(new_client: Any, owner: TestClient, org_id: str, email: str, role: str) -> TestClient:
    """Приглашает email с ролью и возвращает клиент уже вступившего пользователя."""
    token = invite(owner, org_id, email, role).json()["token"]
    c: TestClient = new_client()
    register(c, email)
    r = accept(c, token)
    assert r.status_code == 200, r.text
    return c


def members(client: TestClient, org_id: str) -> list[dict[str, Any]]:
    r = client.get(f"/api/v1/organizations/{org_id}/members")
    assert r.status_code == 200, r.text
    out: list[dict[str, Any]] = r.json()
    return out


def patch_member(client: TestClient, org_id: str, member_id: str, **body: Any) -> Any:
    return client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_id}",
        json=body,
        headers=csrf_headers(client),
    )
