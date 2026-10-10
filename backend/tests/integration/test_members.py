import json
import threading
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import (
    PASSWORD,
    accept,
    csrf_headers,
    invite,
    join,
    login,
    members,
    patch_member,
    register,
    setup_org,
)

NewClient = Callable[[], TestClient]


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


def member_id(client: TestClient, org_id: str, email: str) -> str:
    found: str = next(m["id"] for m in members(client, org_id) if m["email"] == email)
    return found


# ---------------- приглашение: полный цикл ----------------


def test_invite_preview_accept_flow(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    created = invite(owner, org, "Buh@Example.uz", "accountant")
    assert created.status_code == 201
    token = created.json()["token"]
    assert created.json()["email"] == "buh@example.uz" and created.json()["role"] == "accountant"

    anon = new_client()
    preview = anon.get(f"/api/v1/invitations/{token}").json()
    assert preview == {
        "organization_name": "ООО Владелец",
        "role": "accountant",
        "email": "buh@example.uz",
        "state": "pending",
    }

    invitee = new_client()
    register(invitee, "buh@example.uz")
    joined = accept(invitee, token)
    assert joined.status_code == 200 and joined.json()["role"] == "accountant"
    assert joined.json()["permissions"] == [
        "catalog.manage",
        "catalog.read",
        "documents.approve",
        "documents.read",
        "documents.review",
        "documents.upload",
        "members.read",
        "org.read",
        "stock.manage",
        "stock.post",
        "stock.read",
        "warehouses.read",
    ]
    assert [o["id"] for o in invitee.get("/api/v1/organizations").json()] == [org]

    rows = members(owner, org)
    assert {(m["email"], m["role"], m["status"]) for m in rows} == {
        ("owner@example.uz", "owner", "active"),
        ("buh@example.uz", "accountant", "active"),
    }

    # токен одноразовый и не хранится в открытом виде
    again = accept(invitee, token)
    assert again.status_code == 409 and again.json()["code"] == "invitation_used"
    assert anon.get(f"/api/v1/invitations/{token}").json()["state"] == "accepted"
    with admin_engine.connect() as c:
        dump = json.dumps([list(map(str, r)) for r in c.execute(text("SELECT * FROM invitation"))])
        dump += json.dumps(
            [list(map(str, r)) for r in c.execute(text("SELECT * FROM audit_event"))]
        )
    assert token not in dump
    acts = actions(admin_engine)
    assert "member.invited" in acts and "member.joined" in acts


def test_accept_requires_login_matching_email_and_valid_token(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    token = invite(owner, org, "x@example.uz", "storekeeper").json()["token"]

    anon = new_client()
    assert accept(anon, token).status_code == 401

    stranger = new_client()
    register(stranger, "stranger@example.uz")
    wrong = accept(stranger, token)
    assert wrong.status_code == 403 and wrong.json()["code"] == "invitation_wrong_user"
    assert "x@example.uz" not in wrong.text

    assert accept(stranger, "z" * 43).status_code == 404
    assert anon.get(f"/api/v1/invitations/{'z' * 43}").status_code == 404


def test_expired_and_revoked_invitations(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    t1 = invite(owner, org, "late@example.uz", "storekeeper").json()
    t2 = invite(owner, org, "gone@example.uz", "storekeeper").json()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE invitation SET expires_at = now() - interval '1 minute' WHERE email='late@example.uz'"
            )
        )

    late = new_client()
    register(late, "late@example.uz")
    r = accept(late, t1["token"])
    assert r.status_code == 410 and r.json()["code"] == "invitation_expired"
    assert late.get(f"/api/v1/invitations/{t1['token']}").json()["state"] == "expired"

    pending = owner.get(f"/api/v1/organizations/{org}/invitations").json()
    assert {i["email"]: i["expired"] for i in pending} == {
        "late@example.uz": True,
        "gone@example.uz": False,
    }

    d = owner.delete(
        f"/api/v1/organizations/{org}/invitations/{t2['id']}", headers=csrf_headers(owner)
    )
    assert d.status_code == 204
    gone = new_client()
    register(gone, "gone@example.uz")
    r = accept(gone, t2["token"])
    assert r.status_code == 410 and r.json()["code"] == "invitation_revoked"
    assert "member.invitation_revoked" in actions(admin_engine)
    # повторно отозвать нельзя
    assert (
        owner.delete(
            f"/api/v1/organizations/{org}/invitations/{t2['id']}", headers=csrf_headers(owner)
        ).status_code
        == 404
    )


def test_duplicate_invitation_and_existing_member(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    assert invite(owner, org, "dup@example.uz", "accountant").status_code == 201
    dup = invite(owner, org, "DUP@example.uz", "auditor")
    assert dup.status_code == 409 and dup.json()["code"] == "invitation_exists"
    exists = invite(owner, org, "owner@example.uz", "auditor")
    assert exists.status_code == 409 and exists.json()["code"] == "already_member"
    assert invite(owner, org, "n@example.uz", "no_such_role").json()["code"] == "unknown_role"
    assert invite(owner, org, "not-an-email", "auditor").status_code == 422


# ---------------- права: матрица роль × действие ----------------

# роль: (видит участников, управляет участниками)
MATRIX = {
    "owner": (True, True),
    "org_admin": (True, True),
    "accountant": (True, False),
    "storekeeper": (False, False),
    "sales_purchase_manager": (False, False),
    "document_operator": (False, False),
    "auditor": (True, False),
}


@pytest.mark.parametrize("role", list(MATRIX))
def test_role_permission_matrix(new_client: NewClient, admin_engine: Engine, role: str) -> None:
    owner, org = setup_org(new_client)
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage = MATRIX[role]
    base = f"/api/v1/organizations/{org}"

    assert actor.get(base).status_code == 200  # org.read есть у всех
    assert actor.get(f"{base}/members").status_code == (200 if can_read else 403)
    assert actor.get(f"{base}/invitations").status_code == (200 if can_manage else 403)
    r = invite(actor, org, "new@example.uz", "storekeeper")
    assert r.status_code == (201 if can_manage else 403), r.text
    if not can_manage:
        assert "access.denied" in actions(admin_engine)


def test_admin_cannot_touch_owners_but_owner_can(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    admin = join(new_client, owner, org, "admin@example.uz", "org_admin")
    acc = join(new_client, owner, org, "acc@example.uz", "accountant")
    owner_mid = member_id(owner, org, "owner@example.uz")
    acc_mid = member_id(owner, org, "acc@example.uz")

    assert invite(admin, org, "boss@example.uz", "owner").status_code == 403
    assert patch_member(admin, org, owner_mid, status="suspended").status_code == 403
    assert patch_member(admin, org, owner_mid, role_code="auditor").status_code == 403
    assert patch_member(admin, org, acc_mid, role_code="owner").status_code == 403
    # обычное управление админу доступно
    ok = patch_member(admin, org, acc_mid, role_code="storekeeper")
    assert ok.status_code == 200 and ok.json()["role"] == "storekeeper"
    # неуправляющий не может
    assert patch_member(acc, org, acc_mid, role_code="owner").status_code == 403
    # владелец может назначить владельца
    assert patch_member(owner, org, acc_mid, role_code="owner").json()["role"] == "owner"


def test_unknown_role_and_noop_patch(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    join(new_client, owner, org, "acc@example.uz", "accountant")
    mid = member_id(owner, org, "acc@example.uz")
    assert patch_member(owner, org, mid, role_code="nope").json()["code"] == "unknown_role"
    before = actions(admin_engine).count("member.role_changed")
    assert patch_member(owner, org, mid, role_code="accountant").status_code == 200
    assert actions(admin_engine).count("member.role_changed") == before
    assert (
        patch_member(
            owner, org, "00000000-0000-0000-0000-000000000000", status="active"
        ).status_code
        == 404
    )


# ---------------- последний владелец ----------------


def test_last_owner_cannot_be_demoted_or_suspended(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    own = member_id(owner, org, "owner@example.uz")
    r = patch_member(owner, org, own, role_code="auditor")
    assert r.status_code == 409 and r.json()["code"] == "last_owner"
    s = patch_member(owner, org, own, status="suspended")
    assert s.status_code == 422 and s.json()["code"] == "cannot_suspend_self"

    # со вторым владельцем первый может уйти
    join(new_client, owner, org, "second@example.uz", "accountant")
    patch_member(owner, org, member_id(owner, org, "second@example.uz"), role_code="owner")
    assert patch_member(owner, org, own, role_code="auditor").status_code == 200


def test_concurrent_demotion_never_leaves_zero_owners(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    other = join(new_client, owner, org, "second@example.uz", "accountant")
    mid_a = member_id(owner, org, "owner@example.uz")
    mid_b = member_id(owner, org, "second@example.uz")

    from app.main import app

    def reset_two_owners() -> None:
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE membership SET status='active', role_id=(SELECT id FROM role WHERE code='owner') "
                    "WHERE organization_id = CAST(:o AS uuid)"
                ),
                {"o": org},
            )

    def count_active_owners() -> int:
        with admin_engine.connect() as c:
            return int(
                c.execute(
                    text(
                        "SELECT count(*) FROM membership m JOIN role r ON r.id=m.role_id "
                        "WHERE r.code='owner' AND m.status='active' AND m.organization_id = CAST(:o AS uuid)"
                    ),
                    {"o": org},
                ).scalar_one()
            )

    for _round in range(12):
        reset_two_owners()
        barrier = threading.Barrier(2)
        results: list[int] = []

        def demote(
            client: TestClient,
            target: str,
            barrier: threading.Barrier = barrier,
            results: list[int] = results,
        ) -> None:
            with TestClient(app, cookies=dict(client.cookies)) as c:
                headers = csrf_headers(c)
                barrier.wait()
                r = c.patch(
                    f"/api/v1/organizations/{org}/members/{target}",
                    json={"role_code": "auditor"},
                    headers=headers,
                )
                results.append(r.status_code)

        ts = [
            threading.Thread(target=demote, args=(owner, mid_b)),
            threading.Thread(target=demote, args=(other, mid_a)),
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert count_active_owners() >= 1, results


# ---------------- приостановка ----------------


def test_suspended_member_loses_access_and_can_return(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    acc = join(new_client, owner, org, "acc@example.uz", "accountant")
    mid = member_id(owner, org, "acc@example.uz")
    assert acc.get(f"/api/v1/organizations/{org}").status_code == 200

    assert patch_member(owner, org, mid, status="suspended").json()["status"] == "suspended"
    assert acc.get(f"/api/v1/organizations/{org}").status_code == 404
    assert acc.get(f"/api/v1/organizations/{org}/members").status_code == 404
    assert acc.get("/api/v1/organizations").json() == []
    # повторное принятие приглашением невозможно: членство уже существует
    assert patch_member(owner, org, mid, status="active").json()["status"] == "active"
    assert acc.get(f"/api/v1/organizations/{org}").status_code == 200
    acts = actions(admin_engine)
    assert "member.suspended" in acts and "member.reactivated" in acts


def test_login_still_works_for_suspended_member(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    join(new_client, owner, org, "acc@example.uz", "accountant")
    patch_member(owner, org, member_id(owner, org, "acc@example.uz"), status="suspended")
    c = new_client()
    assert login(c, "acc@example.uz", PASSWORD).status_code == 200


def test_roles_directory_requires_login(new_client: NewClient) -> None:
    assert new_client().get("/api/v1/roles").status_code == 401
    c = new_client()
    register(c, "r@example.uz")
    codes = {r["code"] for r in c.get("/api/v1/roles").json()}
    assert codes == set(MATRIX)
