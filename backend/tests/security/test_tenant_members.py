"""Изоляция при управлении участниками: админ одной организации не трогает другую."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import csrf_headers, invite, join, members, patch_member, register, setup_org

NewClient = Callable[[], TestClient]


def _two_orgs(new_client: NewClient) -> tuple[TestClient, str, TestClient, str]:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    return a, org_a, b, org_b


def test_cannot_list_or_invite_in_foreign_org(new_client: NewClient) -> None:
    a, _, _, org_b = _two_orgs(new_client)
    assert a.get(f"/api/v1/organizations/{org_b}/members").status_code == 404
    assert a.get(f"/api/v1/organizations/{org_b}/invitations").status_code == 404
    assert invite(a, org_b, "x@example.uz", "auditor").status_code == 404


def test_foreign_membership_and_invitation_ids_via_own_org_path(new_client: NewClient) -> None:
    a, org_a, b, org_b = _two_orgs(new_client)
    b_member = members(b, org_b)[0]["id"]
    inv_b = invite(b, org_b, "v@example.uz", "auditor").json()["id"]

    # id чужого членства/приглашения под путём СВОЕЙ организации
    assert patch_member(a, org_a, b_member, status="suspended").status_code == 404
    assert (
        a.delete(
            f"/api/v1/organizations/{org_a}/invitations/{inv_b}", headers=csrf_headers(a)
        ).status_code
        == 404
    )
    # и под путём чужой
    assert patch_member(a, org_b, b_member, status="suspended").status_code == 404
    assert members(b, org_b)[0]["status"] == "active"


def test_admin_of_one_org_has_no_rights_in_other(new_client: NewClient) -> None:
    a, org_a, _b, org_b = _two_orgs(new_client)
    admin_a = join(new_client, a, org_a, "adm@example.uz", "org_admin")
    assert admin_a.get(f"/api/v1/organizations/{org_b}/members").status_code == 404


def test_member_of_two_orgs_has_independent_roles(new_client: NewClient) -> None:
    a, org_a, b, org_b = _two_orgs(new_client)
    token = invite(b, org_b, "a@example.uz", "auditor").json()["token"]
    from tests.helpers import accept

    assert accept(a, token).json()["role"] == "auditor"
    assert a.get(f"/api/v1/organizations/{org_a}/invitations").status_code == 200  # владелец в A
    assert a.get(f"/api/v1/organizations/{org_b}/invitations").status_code == 403  # аудитор в B
    assert a.get(f"/api/v1/organizations/{org_b}/members").status_code == 200


# ---------------- RLS на уровне SQL ----------------


def _ctx(conn: Any, user: str = "", org: str = "", token_hash: str = "") -> None:
    conn.execute(
        text(
            "SELECT set_config('app.current_user', :u, true), set_config('app.current_org', :o, true), "
            "set_config('app.invite_token', :t, true)"
        ),
        {"u": user, "o": org, "t": token_hash},
    )


def test_rls_invitation_visibility(
    new_client: NewClient, app_engine: Engine, admin_engine: Engine
) -> None:
    a, org_a, b, org_b = _two_orgs(new_client)
    ta = invite(a, org_a, "x@example.uz", "auditor").json()["token"]
    invite(b, org_b, "y@example.uz", "auditor")

    from app.platform.security import hash_token

    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM invitation")).scalar_one() == 0  # без контекста
    with app_engine.connect() as c:
        _ctx(c, org=org_a)
        assert [r[0] for r in c.execute(text("SELECT email::text FROM invitation"))] == [
            "x@example.uz"
        ]
    with app_engine.connect() as c:
        _ctx(c, token_hash=hash_token(ta).hex())  # владение токеном видно ровно одну строку
        assert [r[0] for r in c.execute(text("SELECT email::text FROM invitation"))] == [
            "x@example.uz"
        ]
    with app_engine.connect() as c:
        _ctx(c, token_hash="00" * 32)
        assert c.execute(text("SELECT count(*) FROM invitation")).scalar_one() == 0


def test_rls_blocks_foreign_invitation_insert_and_update(
    new_client: NewClient, app_engine: Engine
) -> None:
    _a, org_a, b, org_b = _two_orgs(new_client)
    invite(b, org_b, "y@example.uz", "auditor")
    from sqlalchemy.exc import DBAPIError

    with app_engine.connect() as c:
        _ctx(c, org=org_a)
        n = c.execute(text("UPDATE invitation SET revoked_at = now()")).rowcount
        assert n == 0  # чужие приглашения не видны для UPDATE
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO invitation (id, organization_id, email, role_id, token_hash, invited_by, expires_at) "
                    "SELECT gen_random_uuid(), CAST(:o AS uuid), 'z@example.uz', r.id, decode(:h, 'hex'), u.id, now() "
                    "FROM role r, users u LIMIT 1"
                ),
                {"o": org_b, "h": uuid.uuid4().hex},
            )


def test_rls_membership_update_scoped_to_org(new_client: NewClient, app_engine: Engine) -> None:
    _a, org_a, _b, org_b = _two_orgs(new_client)
    with app_engine.connect() as c:
        _ctx(c, org=org_a)
        n = c.execute(
            text(
                "UPDATE membership SET status='suspended' WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org_b},
        ).rowcount
        assert n == 0
    _ = register  # импорт используется в других тестах файла
