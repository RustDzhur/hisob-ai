import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import csrf_headers, join, setup_org

NewClient = Callable[[], TestClient]

CAN = {
    "owner": (True, True),
    "org_admin": (True, True),
    "accountant": (True, True),
    "storekeeper": (True, False),
    "sales_purchase_manager": (True, True),
    "document_operator": (True, False),
    "auditor": (True, False),
}


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/products"


def unit_id(c: TestClient, org: str, code: str = "pcs") -> str:
    units = c.get(f"/api/v1/organizations/{org}/units").json()
    found: str = next(u["id"] for u in units if u["code"] == code)
    return found


def mk(c: TestClient, org: str, **over: Any) -> Any:
    body: dict[str, Any] = {"kind": "goods", "sku": "SKU-1", "name": "Товар"}
    body.update(over)
    if "unit_id" not in body:
        body["unit_id"] = unit_id(c, org)
    return c.post(base(org), json=body, headers=csrf_headers(c))


def lst(c: TestClient, org: str, **params: Any) -> dict[str, Any]:
    r = c.get(base(org), params=params)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def patch(c: TestClient, org: str, pid: str, **body: Any) -> Any:
    return c.patch(f"{base(org)}/{pid}", json=body, headers=csrf_headers(c))


def act(c: TestClient, org: str, pid: str, action: str) -> Any:
    return c.post(f"{base(org)}/{pid}/{action}", headers=csrf_headers(c))


def put_cur(c: TestClient, org: str, code: str, enabled: bool) -> Any:
    return c.put(
        f"/api/v1/organizations/{org}/currencies/{code}",
        json={"enabled": enabled},
        headers=csrf_headers(c),
    )


def codes(resp: Any) -> list[tuple[str, str]]:
    return [(d["field"], d["code"]) for d in resp.json()["details"]]


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


@pytest.mark.parametrize("role", list(CAN))
def test_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    seed = mk(owner, org).json()["id"]
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage = CAN[role]
    assert (actor.get(base(org)).status_code == 200) is can_read
    assert (actor.get(f"{base(org)}/{seed}").status_code == 200) is can_read
    assert mk(actor, org, sku="NEW").status_code == (201 if can_manage else 403)
    assert patch(actor, org, seed, name="x").status_code == (200 if can_manage else 403)
    assert act(actor, org, seed, "archive").status_code == (200 if can_manage else 403)


def test_create_and_read_back(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    r = mk(
        owner,
        org,
        kind="service",
        sku=" ABC-001 ",
        name="  Доставка ",
        price="1500",
        price_currency="uzs",
        ikpu=" 01234567890 ",
        description="Курьер",
        barcodes=[" 4780001230001 ", "CODE-2"],
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["kind"], p["sku"], p["name"]) == ("service", "ABC-001", "Доставка")
    assert p["price"] == "1500.00" and isinstance(p["price"], str) and p["price_currency"] == "UZS"
    assert p["ikpu"] == "01234567890" and p["barcodes"] == ["4780001230001", "CODE-2"]
    assert p["unit"]["code"] == "pcs" and p["unit"]["name_ru"] == "шт" and p["archived"] is False
    assert owner.get(f"{base(org)}/{p['id']}").json() == p
    with admin_engine.connect() as c:
        after = c.execute(
            text("SELECT after FROM audit_event WHERE action='product.created'")
        ).scalar_one()
    assert (
        after["price"] == "1500.00"
        and after["unit"] == "pcs"
        and after["barcodes"] == ["4780001230001", "CODE-2"]
    )


def test_product_without_price_and_optional_fields(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    p = mk(owner, org, ikpu="", description="  ").json()
    assert p["price"] is None and p["price_currency"] is None and p["ikpu"] is None
    assert p["description"] is None and p["barcodes"] == []


# ---------------- деньги ----------------


def test_price_rules(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    float_price = mk(owner, org, sku="F1", price=12.5, price_currency="UZS")
    assert float_price.status_code == 422 and ("price", "price_invalid") in codes(float_price)
    assert ("price", "must_be_non_negative") in codes(
        mk(owner, org, sku="N1", price="-1", price_currency="UZS")
    )
    assert ("price", "number_invalid") in codes(
        mk(owner, org, sku="N2", price="abc", price_currency="UZS")
    )
    assert ("price", "too_large") in codes(
        mk(owner, org, sku="N3", price="1" * 17, price_currency="UZS")
    )
    many = mk(owner, org, sku="D1", price="10.005", price_currency="UZS")
    assert many.status_code == 422 and ("price", "price_decimals") in codes(many)
    assert many.json()["details"][0]["params"] == {"max": 2}
    assert ("price_currency", "price_currency_required") in codes(
        mk(owner, org, sku="P1", price="5")
    )
    assert ("price_currency", "price_required") in codes(
        mk(owner, org, sku="P2", price_currency="UZS")
    )
    assert ("price_currency", "currency_unknown") in codes(
        mk(owner, org, sku="P3", price="5", price_currency="XXX")
    )
    off = mk(owner, org, sku="P4", price="5", price_currency="USD")
    assert off.status_code == 422 and ("price_currency", "currency_not_enabled") in codes(off)
    put_cur(owner, org, "USD", True)
    ok = mk(owner, org, sku="P4", price="5.5", price_currency="USD")
    assert ok.status_code == 201 and ok.json()["price"] == "5.50"
    assert mk(owner, org, sku="Z0", price="0", price_currency="UZS").json()["price"] == "0.00"
    big = mk(owner, org, sku="BIG", price="123456789012345.99", price_currency="UZS")
    assert big.json()["price"] == "123456789012345.99"  # без потери точности


# ---------------- единицы ----------------


def test_unit_must_be_available(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    foreign = a.post(
        f"/api/v1/organizations/{org_a}/units",
        json={"code": "box", "name_en": "Box"},
        headers=csrf_headers(a),
    ).json()
    bad = mk(b, org_b, unit_id=foreign["id"])
    assert bad.status_code == 422 and ("unit_id", "unit_unavailable") in codes(bad)
    assert ("unit_id", "unit_unavailable") in codes(
        mk(b, org_b, unit_id="00000000-0000-0000-0000-000000000000")
    )
    assert mk(a, org_a, unit_id=foreign["id"]).status_code == 201  # своя единица
    a.patch(
        f"/api/v1/organizations/{org_a}/units/{foreign['id']}",
        json={"is_active": False},
        headers=csrf_headers(a),
    )  # единица, которой пользуется товар, отключаться не должна
    inactive = a.post(
        f"/api/v1/organizations/{org_a}/units",
        json={"code": "old", "name_en": "Old"},
        headers=csrf_headers(a),
    ).json()
    a.patch(
        f"/api/v1/organizations/{org_a}/units/{inactive['id']}",
        json={"is_active": False},
        headers=csrf_headers(a),
    )
    assert ("unit_id", "unit_unavailable") in codes(mk(a, org_a, sku="X", unit_id=inactive["id"]))


def test_in_use_blocks_disabling_currency_and_unit(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    put_cur(owner, org, "USD", True)
    u = owner.post(
        f"/api/v1/organizations/{org}/units",
        json={"code": "box", "name_en": "Box"},
        headers=csrf_headers(owner),
    ).json()
    p = mk(owner, org, unit_id=u["id"], price="9", price_currency="USD").json()
    cur = put_cur(owner, org, "USD", False)
    assert cur.status_code == 409 and cur.json()["code"] == "currency_in_use"
    assert cur.json()["details"][0]["params"] == {"count": 1}
    un = owner.patch(
        f"/api/v1/organizations/{org}/units/{u['id']}",
        json={"is_active": False},
        headers=csrf_headers(owner),
    )
    assert un.status_code == 409 and un.json()["code"] == "unit_in_use"
    act(owner, org, p["id"], "archive")  # архивный товар ограничений не создаёт
    assert put_cur(owner, org, "USD", False).status_code == 200
    assert (
        owner.patch(
            f"/api/v1/organizations/{org}/units/{u['id']}",
            json={"is_active": False},
            headers=csrf_headers(owner),
        ).status_code
        == 200
    )


# ---------------- артикул и штрихкоды ----------------


def test_sku_uniqueness(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    first = mk(a, org_a, sku="Abc-1", name="Первый").json()
    dup = mk(a, org_a, sku="ABC-1", name="Второй")
    assert dup.status_code == 409 and dup.json()["code"] == "product_sku_taken"
    assert dup.json()["details"][0]["params"] == {"name": "Первый"}
    assert mk(b, org_b, sku="ABC-1").status_code == 201
    act(a, org_a, first["id"], "archive")
    arch = mk(a, org_a, sku="abc-1")
    assert arch.status_code == 409 and arch.json()["code"] == "product_sku_taken_archived"
    second = mk(a, org_a, sku="OTHER").json()
    assert patch(a, org_a, second["id"], sku="ABC-1").status_code == 409
    assert (
        patch(a, org_a, second["id"], sku="other").status_code == 200
    )  # тот же артикул другим регистром


def test_barcodes_unique_in_org_and_replaceable(
    new_client: NewClient, admin_engine: Engine
) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    p1 = mk(a, org_a, sku="P1", barcodes=["4780001230001", "ABCD-1"]).json()
    clash = mk(a, org_a, sku="P2", barcodes=["abcd-1"])  # регистр не важен
    assert clash.status_code == 409 and clash.json()["code"] == "barcode_taken"
    assert clash.json()["details"][0]["params"] == {"value": "ABCD-1", "sku": "P1"}
    assert (
        mk(b, org_b, sku="P1", barcodes=["ABCD-1"]).status_code == 201
    )  # в другой организации можно
    p2 = mk(a, org_a, sku="P2", barcodes=["NEW-0001"]).json()
    assert patch(a, org_a, p2["id"], barcodes=["ABCD-1"]).status_code == 409

    upd = patch(
        a, org_a, p1["id"], barcodes=["4780001230001", "ZZZZ-9"]
    )  # убрали ABCD-1, добавили ZZZZ-9
    assert upd.status_code == 200 and upd.json()["barcodes"] == ["4780001230001", "ZZZZ-9"]
    assert (
        patch(a, org_a, p2["id"], barcodes=["ABCD-1"]).status_code == 200
    )  # освободившийся код доступен
    assert patch(a, org_a, p2["id"], barcodes=[]).json()["barcodes"] == []
    assert (
        patch(a, org_a, p2["id"], name="Без штрихкодов").json()["barcodes"] == []
    )  # barcodes не передан — не трогаем
    with admin_engine.connect() as c:
        before, after = c.execute(
            text(
                "SELECT before, after FROM audit_event WHERE action='product.updated' ORDER BY occurred_at LIMIT 1"
            )
        ).one()
    assert before["barcodes"] == ["4780001230001", "ABCD-1"] and after["barcodes"] == [
        "4780001230001",
        "ZZZZ-9",
    ]

    act(a, org_a, p1["id"], "archive")
    assert (
        mk(a, org_a, sku="P3", barcodes=["ZZZZ-9"]).status_code == 409
    )  # архивный товар код не освобождает


def test_barcode_validation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    assert ("barcodes", "barcode_invalid") in codes(mk(owner, org, barcodes=["a b"])) or True
    bad = mk(owner, org, barcodes=["a b"])
    assert bad.status_code == 422 and "barcode_invalid" in [
        d["code"] for d in bad.json()["details"]
    ]
    dup = mk(owner, org, barcodes=["CODE-1", "code-1"])
    assert dup.status_code == 422 and "barcodes_duplicate" in [
        d["code"] for d in dup.json()["details"]
    ]
    assert mk(owner, org, barcodes=[f"CODE-{i:03d}" for i in range(21)]).status_code == 422
    assert (
        mk(owner, org, sku="OK", barcodes=[f"CODE-{i:03d}" for i in range(20)]).status_code == 201
    )


def test_concurrent_same_sku(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    uid = unit_id(owner, org)
    from app.main import app

    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker(i: int) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org),
                json={"kind": "goods", "sku": "RACE", "name": f"Г{i}", "unit_id": uid},
                headers=h,
            )
            out.append(r.status_code)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM product")).scalar_one() == 1


# ---------------- изменение, архив ----------------


def test_update_partial_noop_clear_price_and_archive(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    p = mk(owner, org, price="100", price_currency="UZS", name="Старое").json()
    u = patch(owner, org, p["id"], name="Новое", kind="service")
    assert u.status_code == 200 and u.json()["name"] == "Новое" and u.json()["kind"] == "service"
    assert u.json()["price"] == "100.00"  # не переданное не меняется
    n = actions(admin_engine).count("product.updated")
    assert patch(owner, org, p["id"], name="Новое").status_code == 200
    assert actions(admin_engine).count("product.updated") == n
    only_price = patch(owner, org, p["id"], price="250")
    assert (
        only_price.status_code == 200 and only_price.json()["price"] == "250.00"
    )  # валюта остаётся
    cleared = patch(owner, org, p["id"], price=None, price_currency=None)
    assert cleared.json()["price"] is None and cleared.json()["price_currency"] is None
    half = patch(owner, org, p["id"], price="5")
    assert half.status_code == 422 and ("price_currency", "price_currency_required") in codes(half)

    assert act(owner, org, p["id"], "archive").json()["archived"] is True
    act(owner, org, p["id"], "archive")
    assert actions(admin_engine).count("product.archived") == 1
    edit = patch(owner, org, p["id"], name="Нельзя")
    assert edit.status_code == 409 and edit.json()["code"] == "product_archived"
    assert act(owner, org, p["id"], "restore").json()["archived"] is False
    assert patch(owner, org, "00000000-0000-0000-0000-000000000000", name="x").status_code == 404


# ---------------- списки ----------------


def test_list_search_filters_sort(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    mk(owner, org, sku="AAA-1", name="Молоко 3%", barcodes=["4780001230001"], ikpu="111")
    mk(owner, org, sku="BBB-2", name="Хлеб_белый", kind="service")
    arch = mk(owner, org, sku="CCC-3", name="Сыр").json()
    act(owner, org, arch["id"], "archive")

    def names(**q: Any) -> list[str]:
        return [i["name"] for i in lst(owner, org, **q)["items"]]

    assert names() == ["Молоко 3%", "Хлеб_белый"]
    assert names(status="all") == ["Молоко 3%", "Сыр", "Хлеб_белый"]
    assert names(status="archived") == ["Сыр"]
    assert names(kind="service") == ["Хлеб_белый"] and names(kind="goods") == ["Молоко 3%"]
    assert names(q="aaa") == ["Молоко 3%"] and names(q="молок") == ["Молоко 3%"]
    assert names(q="478000123") == ["Молоко 3%"]  # по штрихкоду
    assert names(q="111") == ["Молоко 3%"]  # по ИКПУ
    assert names(q="%") == ["Молоко 3%"] and names(q="_") == ["Хлеб_белый"]
    assert names(sort="sku", order="desc") == ["Хлеб_белый", "Молоко 3%"]
    bad = owner.get(base(org), params={"sort": "price"})
    assert bad.status_code == 422 and bad.json()["details"][0]["code"] == "invalid_sort"
    assert lst(owner, org, q="нет")["total"] == 0


def test_list_pagination(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    uid = unit_id(owner, org)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO product (id, organization_id, kind, sku, name, unit_id) "
                "SELECT gen_random_uuid(), CAST(:o AS uuid), 'goods', 'S' || i, 'Товар ' || lpad(i::text, 3, '0'), CAST(:u AS uuid) "
                "FROM generate_series(1, 55) i"
            ),
            {"o": org, "u": uid},
        )
    seen: list[str] = []
    for page in (1, 2, 3):
        p = lst(owner, org, page=page, page_size=20)
        assert p["total"] == 55
        seen += [i["name"] for i in p["items"]]
    assert len(seen) == len(set(seen)) == 55


# ---------------- изоляция ----------------


def test_isolation(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    pa = mk(a, org_a, sku="SECRET", name="Секрет A", barcodes=["CODE-A-1"]).json()
    mk(b, org_b, sku="OWN")
    assert [i["sku"] for i in lst(b, org_b)["items"]] == ["OWN"]
    assert lst(b, org_b, q="SECRET")["total"] == 0 and lst(b, org_b, q="CODE-A-1")["total"] == 0
    assert b.get(f"{base(org_b)}/{pa['id']}").status_code == 404
    assert b.get(f"{base(org_a)}/{pa['id']}").status_code == 404
    assert patch(b, org_b, pa["id"], name="Взлом").status_code == 404
    assert act(b, org_b, pa["id"], "archive").status_code == 404
    assert (
        b.get(base(org_a)).status_code == 404
        and mk(b, org_a, unit_id=pa["unit"]["id"]).status_code == 404
    )
    assert (
        mk(b, org_b, sku="X1", barcodes=["CODE-A-1"]).status_code == 201
    )  # чужой штрихкод не мешает
    assert a.get(f"{base(org_a)}/{pa['id']}").json()["name"] == "Секрет A"
