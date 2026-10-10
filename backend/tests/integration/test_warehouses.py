import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import csrf_headers, join, setup_org

NewClient = Callable[[], TestClient]

# роль: (читает, управляет) — docs/06-stage2-plan.md §4: склады ведёт кладовщик
CAN = {
    "owner": (True, True),
    "org_admin": (True, True),
    "accountant": (True, False),
    "storekeeper": (True, True),
    "sales_purchase_manager": (True, False),
    "document_operator": (True, False),
    "auditor": (True, False),
}


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/warehouses"


def mk(c: TestClient, org: str, **body: Any) -> Any:
    body.setdefault("name", "Главный склад")
    return c.post(base(org), json=body, headers=csrf_headers(c))


def lst(c: TestClient, org: str, **params: Any) -> dict[str, Any]:
    r = c.get(base(org), params=params)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def patch(c: TestClient, org: str, wid: str, **body: Any) -> Any:
    return c.patch(f"{base(org)}/{wid}", json=body, headers=csrf_headers(c))


def act(c: TestClient, org: str, wid: str, action: str) -> Any:
    return c.post(f"{base(org)}/{wid}/{action}", headers=csrf_headers(c))


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


@pytest.mark.parametrize("role", list(CAN))
def test_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    seed = mk(owner, org, name="Seed").json()["id"]
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage = CAN[role]
    assert (actor.get(base(org)).status_code == 200) is can_read
    assert (actor.get(f"{base(org)}/{seed}").status_code == 200) is can_read
    assert mk(actor, org, name=f"W-{role}").status_code == (201 if can_manage else 403)
    assert patch(actor, org, seed, address="x").status_code == (200 if can_manage else 403)
    assert act(actor, org, seed, "archive").status_code == (200 if can_manage else 403)


def test_create_normalize_get_audit(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    r = mk(owner, org, name="  Центральный  склад ", address=" Ташкент, Чиланзар ", notes="")
    assert r.status_code == 201
    w = r.json()
    assert (
        w["name"] == "Центральный  склад"
        and w["address"] == "Ташкент, Чиланзар"
        and w["notes"] is None
    )
    assert w["archived"] is False
    assert owner.get(f"{base(org)}/{w['id']}").json() == w
    with admin_engine.connect() as c:
        after = c.execute(
            text("SELECT after FROM audit_event WHERE action='warehouse.created'")
        ).scalar_one()
    assert after["name"] == "Центральный  склад"


def test_validation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    r = owner.post(base(org), json={}, headers=csrf_headers(owner))
    assert r.status_code == 422 and r.json()["details"][0]["code"] == "required"
    blank = mk(owner, org, name="   ")
    assert blank.status_code == 422 and blank.json()["details"][0]["code"] == "too_short"
    assert mk(owner, org, name="x" * 201).status_code == 422
    assert mk(owner, org, name="OK", address="a" * 501).status_code == 422


def test_name_unique_per_org_case_insensitive(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    first = mk(a, org_a, name="Склад №1").json()
    dup = mk(a, org_a, name="СКЛАД №1")
    assert dup.status_code == 409 and dup.json()["code"] == "warehouse_name_taken"
    assert mk(b, org_b, name="Склад №1").status_code == 201  # в другой организации можно
    act(a, org_a, first["id"], "archive")
    arch = mk(a, org_a, name="склад №1")
    assert arch.status_code == 409 and arch.json()["code"] == "warehouse_name_taken_archived"
    other = mk(a, org_a, name="Склад №2").json()
    assert patch(a, org_a, other["id"], name="склад №1").status_code == 409
    assert (
        patch(a, org_a, other["id"], name="СКЛАД №2").status_code == 200
    )  # свой регистр можно менять


def test_concurrent_create_same_name(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker(i: int) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            out.append(c.post(base(org), json={"name": "Гонка"}, headers=h).status_code)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM warehouse")).scalar_one() == 1


def test_update_partial_noop_and_archive_cycle(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    w = mk(owner, org, name="Старый", address="Адрес 1").json()
    u = patch(owner, org, w["id"], name="Новый", address=None)
    assert u.json()["name"] == "Новый" and u.json()["address"] is None
    n = actions(admin_engine).count("warehouse.updated")
    assert patch(owner, org, w["id"], name="Новый").status_code == 200
    assert actions(admin_engine).count("warehouse.updated") == n
    with admin_engine.connect() as c:
        before, after = c.execute(
            text("SELECT before, after FROM audit_event WHERE action='warehouse.updated'")
        ).one()
    assert (
        before["name"] == "Старый" and after["name"] == "Новый" and before["address"] == "Адрес 1"
    )

    assert act(owner, org, w["id"], "archive").json()["archived"] is True
    act(owner, org, w["id"], "archive")
    assert actions(admin_engine).count("warehouse.archived") == 1
    edit = patch(owner, org, w["id"], name="Нельзя")
    assert edit.status_code == 409 and edit.json()["code"] == "warehouse_archived"
    assert lst(owner, org)["total"] == 0 and lst(owner, org, status="archived")["total"] == 1
    assert act(owner, org, w["id"], "restore").json()["archived"] is False
    assert patch(owner, org, w["id"], name="Можно").status_code == 200
    assert patch(owner, org, "00000000-0000-0000-0000-000000000000", name="x").status_code == 404


def test_list_search_sort_pagination(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    mk(owner, org, name="Бета", address="Самарканд")
    mk(owner, org, name="Альфа 100%", address="Ташкент")
    mk(owner, org, name="Гамма_1")

    def names(**q: Any) -> list[str]:
        return [i["name"] for i in lst(owner, org, **q)["items"]]

    assert names() == ["Альфа 100%", "Бета", "Гамма_1"]
    assert names(sort="name", order="desc") == ["Гамма_1", "Бета", "Альфа 100%"]
    assert names(q="самарканд") == ["Бета"]  # по адресу, регистр не важен
    assert names(q="%") == ["Альфа 100%"] and names(q="_") == ["Гамма_1"]
    assert (
        owner.get(base(org), params={"sort": "address"}).json()["details"][0]["code"]
        == "invalid_sort"
    )

    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO warehouse (id, organization_id, name) "
                "SELECT gen_random_uuid(), CAST(:o AS uuid), 'Склад ' || lpad(i::text, 3, '0') FROM generate_series(1, 55) i"
            ),
            {"o": org},
        )
    seen: list[str] = []
    for page in (1, 2, 3):
        p = lst(owner, org, page=page, page_size=20)
        assert p["total"] == 58
        seen += [i["name"] for i in p["items"]]
    assert len(seen) == len(set(seen)) == 58


def test_isolation(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    wa = mk(a, org_a, name="Секрет A").json()
    mk(b, org_b, name="Свой")
    assert [i["name"] for i in lst(b, org_b)["items"]] == ["Свой"]
    assert lst(b, org_b, q="Секрет")["total"] == 0
    assert b.get(f"{base(org_b)}/{wa['id']}").status_code == 404
    assert b.get(f"{base(org_a)}/{wa['id']}").status_code == 404
    assert patch(b, org_b, wa["id"], name="Взлом").status_code == 404
    assert act(b, org_b, wa["id"], "archive").status_code == 404
    assert b.get(base(org_a)).status_code == 404 and mk(b, org_a).status_code == 404
    assert a.get(f"{base(org_a)}/{wa['id']}").json()["name"] == "Секрет A"
