import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import csrf_headers, join, setup_org

NewClient = Callable[[], TestClient]

CAN = {  # роль: (читает, управляет)
    "owner": (True, True),
    "org_admin": (True, True),
    "accountant": (True, True),
    "storekeeper": (True, False),
    "sales_purchase_manager": (True, True),
    "document_operator": (True, False),
    "auditor": (True, False),
}


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/counterparties"


def create(c: TestClient, org: str, **body: Any) -> Any:
    body.setdefault("name", "ООО Ромашка")
    return c.post(base(org), json=body, headers=csrf_headers(c))


def lst(c: TestClient, org: str, **params: Any) -> dict[str, Any]:
    r = c.get(base(org), params=params)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def patch(c: TestClient, org: str, cid: str, **body: Any) -> Any:
    return c.patch(f"{base(org)}/{cid}", json=body, headers=csrf_headers(c))


def post_action(c: TestClient, org: str, cid: str, action: str) -> Any:
    return c.post(f"{base(org)}/{cid}/{action}", headers=csrf_headers(c))


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


def bulk(engine: Engine, org: str, n: int, prefix: str = "Контрагент") -> None:
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO counterparty (id, organization_id, name, tin) "
                "SELECT gen_random_uuid(), CAST(:o AS uuid), :p || ' ' || lpad(i::text, 3, '0'), "
                "lpad((100000000 + i)::text, 9, '0') FROM generate_series(1, :n) i"
            ),
            {"o": org, "p": prefix, "n": n},
        )


@pytest.mark.parametrize("role", list(CAN))
def test_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    seed = create(owner, org, name="Seed").json()["id"]
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage = CAN[role]
    assert (actor.get(base(org)).status_code == 200) is can_read
    assert (actor.get(f"{base(org)}/{seed}").status_code == 200) is can_read
    assert create(actor, org, name="X").status_code == (201 if can_manage else 403)
    assert patch(actor, org, seed, notes="n").status_code == (200 if can_manage else 403)
    assert post_action(actor, org, seed, "archive").status_code == (200 if can_manage else 403)


def test_create_get_normalization_and_audit(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    r = create(
        owner,
        org,
        name="  ООО  Ромашка ",
        tin=" 123456789 ",
        is_customer=True,
        email="Sales@Example.UZ",
        phone="+998 90 123-45-67",
        address=" Ташкент ",
        notes="",
    )
    assert r.status_code == 201
    c = r.json()
    assert c["name"] == "ООО  Ромашка" and c["tin"] == "123456789"
    assert c["email"] == "sales@example.uz" and c["address"] == "Ташкент" and c["notes"] is None
    assert c["is_customer"] is True and c["is_supplier"] is False and c["archived"] is False
    assert owner.get(f"{base(org)}/{c['id']}").json() == c
    with admin_engine.connect() as conn:
        after = conn.execute(
            text("SELECT after FROM audit_event WHERE action='counterparty.created'")
        ).scalar_one()
    assert after["tin"] == "123456789" and after["archived"] is False


@pytest.mark.parametrize(
    "body,field,code",
    [
        ({"name": ""}, "name", "too_short"),
        ({"name": "x" * 201}, "name", "too_long"),
        ({"name": "A", "tin": "12ab"}, "tin", "tin_invalid"),
        ({"name": "A", "email": "nope"}, "email", "email_invalid"),
        ({"name": "A", "phone": "abc"}, "phone", "phone_invalid"),
    ],
)
def test_validation_codes(
    new_client: NewClient, body: dict[str, Any], field: str, code: str
) -> None:
    owner, org = setup_org(new_client)
    r = owner.post(base(org), json=body, headers=csrf_headers(owner))
    assert r.status_code == 422
    assert (field, code) in [(d["field"], d["code"]) for d in r.json()["details"]]


def test_missing_name_and_blank_tin_allowed_many_times(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    miss = owner.post(base(org), json={}, headers=csrf_headers(owner))
    assert miss.status_code == 422 and miss.json()["details"][0]["code"] == "required"
    assert create(owner, org, name="A", tin="").status_code == 201
    assert create(owner, org, name="B", tin="   ").status_code == 201  # пустой ИНН не конфликтует
    assert create(owner, org, name="C").status_code == 201


def test_tin_unique_in_org_and_reports_existing_name(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    first = create(a, org_a, name="ООО Первый", tin="123456789").json()
    dup = create(a, org_a, name="ООО Второй", tin="123456789")
    d = dup.json()
    assert dup.status_code == 409 and d["code"] == "counterparty_tin_taken"
    assert d["details"][0]["field"] == "tin" and d["details"][0]["params"] == {"name": "ООО Первый"}
    assert create(b, org_b, name="В другой организации", tin="123456789").status_code == 201

    post_action(a, org_a, first["id"], "archive")
    arch = create(a, org_a, name="Снова", tin="123456789")
    assert arch.status_code == 409 and arch.json()["code"] == "counterparty_tin_taken_archived"

    second = create(a, org_a, name="Второй", tin="987654321").json()
    clash = patch(a, org_a, second["id"], tin="123456789")
    assert clash.status_code == 409
    assert (
        patch(a, org_a, second["id"], tin="987654321", name="Второй 2").status_code == 200
    )  # свой ИНН ок


def test_concurrent_creates_with_same_tin(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    barrier = threading.Barrier(4)
    codes: list[int] = []

    def worker(i: int) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            headers = csrf_headers(c)
            barrier.wait()
            r = c.post(base(org), json={"name": f"Гонка {i}", "tin": "555555555"}, headers=headers)
            codes.append(r.status_code)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes) == [201, 409, 409, 409], codes
    with admin_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM counterparty")).scalar_one() == 1


def test_update_partial_audit_and_noop(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    c = create(owner, org, name="Старое", tin="123456789", phone="+998901234567").json()
    r = patch(owner, org, c["id"], name="Новое", is_supplier=True, phone=None)
    assert r.status_code == 200
    u = r.json()
    assert (
        u["name"] == "Новое"
        and u["is_supplier"] is True
        and u["phone"] is None
        and u["tin"] == "123456789"
    )
    n = actions(admin_engine).count("counterparty.updated")
    assert patch(owner, org, c["id"], name="Новое").status_code == 200  # без изменений
    assert actions(admin_engine).count("counterparty.updated") == n
    with admin_engine.connect() as conn:
        before, after = conn.execute(
            text("SELECT before, after FROM audit_event WHERE action='counterparty.updated'")
        ).one()
    assert (
        before["name"] == "Старое"
        and after["name"] == "Новое"
        and before["phone"] == "+998901234567"
    )
    assert patch(owner, org, c["id"], tin="bad").status_code == 422
    assert patch(owner, org, "00000000-0000-0000-0000-000000000000", name="x").status_code == 404


def test_archive_restore_cycle(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    c = create(owner, org, name="Архивный").json()
    assert post_action(owner, org, c["id"], "archive").json()["archived"] is True
    post_action(owner, org, c["id"], "archive")  # повтор — без новой записи
    assert actions(admin_engine).count("counterparty.archived") == 1
    edit = patch(owner, org, c["id"], name="Нельзя")
    assert edit.status_code == 409 and edit.json()["code"] == "counterparty_archived"
    assert owner.get(f"{base(org)}/{c['id']}").json()["archived"] is True  # читать можно
    assert lst(owner, org)["total"] == 0 and lst(owner, org, status="archived")["total"] == 1
    assert post_action(owner, org, c["id"], "restore").json()["archived"] is False
    assert patch(owner, org, c["id"], name="Можно").status_code == 200
    assert {"counterparty.archived", "counterparty.restored"} <= set(actions(admin_engine))


# ---------------- списки ----------------


def test_pagination_is_complete_and_stable(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    bulk(admin_engine, org, 55)
    seen: list[str] = []
    for page in (1, 2, 3):
        p = lst(owner, org, page=page, page_size=20)
        assert p["total"] == 55 and p["page"] == page and p["page_size"] == 20
        seen += [i["name"] for i in p["items"]]
    assert len(seen) == 55 and len(set(seen)) == 55 and seen == sorted(seen)
    assert lst(owner, org, page=4, page_size=20)["items"] == []
    assert lst(owner, org)["page_size"] == 25 and len(lst(owner, org)["items"]) == 25
    for bad in ({"page": 0}, {"page_size": 0}, {"page_size": 101}, {"order": "up"}):
        assert owner.get(base(org), params=bad).status_code == 422


def test_sorting(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    for name, tin in (("Бета", "222222222"), ("Альфа", "333333333"), ("Гамма", "111111111")):
        create(owner, org, name=name, tin=tin)
    assert [i["name"] for i in lst(owner, org)["items"]] == ["Альфа", "Бета", "Гамма"]
    assert [i["name"] for i in lst(owner, org, sort="name", order="desc")["items"]] == [
        "Гамма",
        "Бета",
        "Альфа",
    ]
    assert [i["tin"] for i in lst(owner, org, sort="tin")["items"]] == [
        "111111111",
        "222222222",
        "333333333",
    ]
    assert len(lst(owner, org, sort="created_at", order="desc")["items"]) == 3
    bad = owner.get(base(org), params={"sort": "password; drop table"})
    assert bad.status_code == 422 and bad.json()["details"][0]["code"] == "invalid_sort"


def test_search_and_wildcards_are_literal(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    create(
        owner,
        org,
        name="ООО Ромашка",
        tin="123456789",
        email="info@romashka.uz",
        phone="+998 90 111-22-33",
    )
    create(owner, org, name="Tashkent 100% Trade", tin="987654321")
    create(owner, org, name="Under_score LLC")

    def names(**q: Any) -> list[str]:
        return sorted(i["name"] for i in lst(owner, org, **q)["items"])

    assert names(q="ромаш") == ["ООО Ромашка"]  # регистр и часть слова
    assert names(q="123456") == ["ООО Ромашка"]  # по ИНН
    assert names(q="romashka.uz") == ["ООО Ромашка"]  # по email
    assert names(q="111-22") == ["ООО Ромашка"]  # по телефону
    assert names(q="%") == ["Tashkent 100% Trade"]  # % — обычный символ
    assert names(q="_") == ["Under_score LLC"]  # _ — обычный символ
    assert names(q="\\") == []
    assert names(q="нет такого") == [] and lst(owner, org, q="нет такого")["total"] == 0
    assert len(names(q="   ")) == 3  # пустой поиск = без фильтра


def test_role_and_status_filters_with_totals(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = create(owner, org, name="A", is_customer=True).json()
    create(owner, org, name="B", is_supplier=True)
    create(owner, org, name="C", is_customer=True, is_supplier=True)
    create(owner, org, name="D")
    post_action(owner, org, a["id"], "archive")

    def count(**q: Any) -> int:
        total: int = lst(owner, org, **q)["total"]
        return total

    assert count() == 3 and count(status="all") == 4 and count(status="archived") == 1
    assert count(role="customer") == 1 and count(role="supplier") == 2
    assert count(role="customer", status="all") == 2
    assert count(role="supplier", q="C") == 1


# ---------------- изоляция ----------------


def test_isolation_between_orgs(new_client: NewClient, admin_engine: Engine) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    ca = create(a, org_a, name="Секрет A", tin="123456789").json()
    create(b, org_b, name="Секрет B")
    assert [i["name"] for i in lst(a, org_a)["items"]] == ["Секрет A"]
    assert lst(b, org_b, q="Секрет A")["total"] == 0
    assert b.get(f"{base(org_b)}/{ca['id']}").status_code == 404  # чужой id под своей организацией
    assert b.get(f"{base(org_a)}/{ca['id']}").status_code == 404  # чужая организация
    assert patch(b, org_b, ca["id"], name="Взлом").status_code == 404
    assert post_action(b, org_b, ca["id"], "archive").status_code == 404
    assert b.get(base(org_a)).status_code == 404 and create(b, org_a).status_code == 404
    assert a.get(f"{base(org_a)}/{ca['id']}").json()["name"] == "Секрет A"
    assert "Секрет" not in " ".join(actions(admin_engine))  # в имена действий данные не попадают
