import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import invite, join, patch_member, register, setup_org

NewClient = Callable[[], TestClient]


def audit(client: TestClient, org: str, **params: object) -> dict:  # type: ignore[type-arg]
    r = client.get(f"/api/v1/organizations/{org}/audit", params=params)
    assert r.status_code == 200, r.text
    body: dict = r.json()  # type: ignore[type-arg]
    return body


CAN_READ = {
    "owner": True,
    "org_admin": True,
    "auditor": True,
    "accountant": False,
    "storekeeper": False,
    "sales_purchase_manager": False,
    "document_operator": False,
}


@pytest.mark.parametrize("role", list(CAN_READ))
def test_audit_read_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    r = actor.get(f"/api/v1/organizations/{org}/audit")
    assert r.status_code == (200 if CAN_READ[role] else 403)
    a = actor.get(f"/api/v1/organizations/{org}/audit/actions")
    assert a.status_code == (200 if CAN_READ[role] else 403)


def test_audit_content_actor_and_order(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    invite(owner, org, "new@example.uz", "auditor")
    page = audit(owner, org)
    actions = [e["action"] for e in page["items"]]
    assert actions[0] == "member.invited" and actions[-1] == "org.created"  # новые сверху
    times = [e["occurred_at"] for e in page["items"]]
    assert times == sorted(times, reverse=True)
    first = page["items"][0]
    assert first["actor"]["email"] == "owner@example.uz"
    assert first["after"]["email"] == "new@example.uz" and first["correlation_id"]
    assert page["total"] == len(page["items"]) == 2  # события входа (без организации) не попадают


def test_audit_pagination_filters_and_limits(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    for i in range(7):
        invite(owner, org, f"u{i}@example.uz", "auditor")
    p1 = audit(owner, org, page=1, page_size=3)
    p2 = audit(owner, org, page=2, page_size=3)
    p3 = audit(owner, org, page=3, page_size=3)
    ids = [e["id"] for p in (p1, p2, p3) for e in p["items"]]
    assert p1["total"] == 8 and len(p1["items"]) == len(p2["items"]) == 3 and len(p3["items"]) == 2
    assert len(set(ids)) == 8  # без повторов и пропусков
    assert audit(owner, org, page=9, page_size=3)["items"] == []

    only = audit(owner, org, action="member.invited", page_size=100)
    assert only["total"] == 7 and {e["action"] for e in only["items"]} == {"member.invited"}
    assert audit(owner, org, action="nope")["total"] == 0
    me = owner.get("/api/v1/auth/me").json()["id"]
    assert audit(owner, org, actor_user_id=me)["total"] == 8
    assert owner.get(f"/api/v1/organizations/{org}/audit/actions").json() == [
        "member.invited",
        "org.created",
    ]

    for bad in ({"page_size": 101}, {"page_size": 0}, {"page": 0}, {"date_from": "2026-13-01"}):
        assert owner.get(f"/api/v1/organizations/{org}/audit", params=bad).status_code == 422
    inv = owner.get(
        f"/api/v1/organizations/{org}/audit",
        params={"date_from": "2026-12-02", "date_to": "2026-12-01"},
    )
    assert inv.status_code == 422


def test_audit_date_filter_uses_org_timezone(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)  # Asia/Tashkent = UTC+5
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE audit_event DISABLE TRIGGER audit_event_no_update_delete")
            if False
            else text("SELECT 1")
        )
        c.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_update_delete"))
        # 2026-03-10 20:00 UTC = 2026-03-11 01:00 по Ташкенту
        c.execute(
            text(
                "UPDATE audit_event SET occurred_at = '2026-03-10T20:00:00+00' WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        )
        c.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_update_delete"))
    assert audit(owner, org, date_from="2026-03-11", date_to="2026-03-11")["total"] == 1
    assert audit(owner, org, date_from="2026-03-10", date_to="2026-03-10")["total"] == 0


def test_audit_records_denials_and_role_changes(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    acc = join(new_client, owner, org, "acc@example.uz", "accountant")
    acc.get(f"/api/v1/organizations/{org}/audit")  # отказ
    mid = next(
        m["id"]
        for m in owner.get(f"/api/v1/organizations/{org}/members").json()
        if m["email"] == "acc@example.uz"
    )
    patch_member(owner, org, mid, role_code="auditor")
    actions = {e["action"] for e in audit(owner, org, page_size=100)["items"]}
    assert {
        "access.denied",
        "member.role_changed",
        "member.joined",
        "member.invited",
        "org.created",
    } <= actions
    denied = next(e for e in audit(owner, org, action="access.denied")["items"])
    assert (
        denied["after"] == {"permission": "audit.read"}
        and denied["actor"]["email"] == "acc@example.uz"
    )


def test_audit_never_contains_secrets(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    token = invite(owner, org, "x@example.uz", "auditor").json()["token"]
    dump = json.dumps(audit(owner, org, page_size=100))
    assert token not in dump and "password" not in dump.lower() and "token_hash" not in dump


def test_audit_isolated_between_orgs(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    invite(b, org_b, "only-b@example.uz", "auditor")
    assert a.get(f"/api/v1/organizations/{org_b}/audit").status_code == 404
    assert a.get(f"/api/v1/organizations/{org_b}/audit/actions").status_code == 404
    emails = json.dumps(audit(a, org_a, page_size=100))
    assert "only-b@example.uz" not in emails and "b@example.uz" not in emails
    assert audit(a, org_a)["total"] == 1


def test_suspended_auditor_cannot_read(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    aud = join(new_client, owner, org, "aud@example.uz", "auditor")
    assert aud.get(f"/api/v1/organizations/{org}/audit").status_code == 200
    mid = next(
        m["id"]
        for m in owner.get(f"/api/v1/organizations/{org}/members").json()
        if m["email"] == "aud@example.uz"
    )
    patch_member(owner, org, mid, status="suspended")
    assert aud.get(f"/api/v1/organizations/{org}/audit").status_code == 404


def _noop() -> None:
    _ = register
