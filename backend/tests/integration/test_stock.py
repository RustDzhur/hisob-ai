import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import csrf_headers, join, setup_org

NewClient = Callable[[], TestClient]

# роль: (читает, ведёт черновики, проводит) — docs/08-stage4-plan.md (Q24)
CAN = {
    "owner": (True, True, True),
    "org_admin": (True, True, True),
    "accountant": (True, True, True),
    "storekeeper": (True, True, True),
    "sales_purchase_manager": (True, True, False),
    "document_operator": (True, False, False),
    "auditor": (True, False, False),
}


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/stock"


def api(org: str, path: str) -> str:
    return f"/api/v1/organizations/{org}{path}"


def warehouse(c: TestClient, org: str, name: str = "Главный склад") -> str:
    r = c.post(api(org, "/warehouses"), json={"name": name}, headers=csrf_headers(c))
    assert r.status_code == 201, r.text
    wid: str = r.json()["id"]
    return wid


def product(c: TestClient, org: str, sku: str, kind: str = "goods", unit_code: str = "pcs") -> str:
    units = c.get(api(org, "/units")).json()
    uid = next(u["id"] for u in units if u["code"] == unit_code)
    r = c.post(
        api(org, "/products"),
        json={"kind": kind, "sku": sku, "name": f"Товар {sku}", "unit_id": uid},
        headers=csrf_headers(c),
    )
    assert r.status_code == 201, r.text
    pid: str = r.json()["id"]
    return pid


def op_body(kind: str, wid: str, lines: list[tuple[str, str]], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": kind,
        "op_date": "2026-10-01",
        "warehouse_id": wid,
        "lines": [{"product_id": p, "quantity": q} for p, q in lines],
    }
    body.update(extra)
    return body


def create(c: TestClient, org: str, body: dict[str, Any]) -> Any:
    return c.post(base(org) + "/operations", json=body, headers=csrf_headers(c))


def post_op(c: TestClient, org: str, oid: str, version: int | None = None) -> Any:
    if version is None:
        version = c.get(f"{base(org)}/operations/{oid}").json()["version"]
    return c.post(
        f"{base(org)}/operations/{oid}/post",
        json={"expected_version": version},
        headers=csrf_headers(c),
    )


def do(
    c: TestClient, org: str, kind: str, wid: str, lines: list[tuple[str, str]], **extra: Any
) -> dict[str, Any]:
    """Создаёт и проводит операцию; ожидает успех."""
    r = create(c, org, op_body(kind, wid, lines, **extra))
    assert r.status_code == 201, r.text
    p = post_op(c, org, r.json()["id"])
    assert p.status_code == 200, p.text
    out: dict[str, Any] = p.json()
    return out


def balances(c: TestClient, org: str, **params: Any) -> list[dict[str, Any]]:
    r = c.get(base(org) + "/balances", params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


def qty(c: TestClient, org: str, wid: str, pid: str) -> str:
    for b in balances(c, org, include_zero=True):
        if b["warehouse"]["id"] == wid and b["product_id"] == pid:
            return str(b["quantity"])
    return "0"


def reconciliation(c: TestClient, org: str) -> dict[str, Any]:
    r = c.get(base(org) + "/reconciliation")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


# ---------------- права ----------------


@pytest.mark.parametrize("role", list(CAN))
def test_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    pid = product(owner, org, "P1")
    seed = create(owner, org, op_body("receipt", wid, [(pid, "5")])).json()["id"]
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage, can_post = CAN[role]
    assert (actor.get(base(org) + "/balances").status_code == 200) is can_read
    assert (actor.get(base(org) + "/operations").status_code == 200) is can_read
    assert (actor.get(base(org) + "/movements").status_code == 200) is can_read
    assert (actor.get(base(org) + "/reconciliation").status_code == 200) is can_read
    mine = create(actor, org, op_body("receipt", wid, [(pid, "1")]))
    assert mine.status_code == (201 if can_manage else 403)
    assert post_op(actor, org, seed, 1).status_code == (200 if can_post else 403)


def test_negative_policy_setting_requires_org_manage(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert owner.get(base(org) + "/settings").json() == {"negative_stock_policy": "forbid"}
    body = {"negative_stock_policy": "allow"}
    assert (
        keeper.put(base(org) + "/settings", json=body, headers=csrf_headers(keeper)).status_code
        == 403
    )
    assert (
        owner.put(base(org) + "/settings", json=body, headers=csrf_headers(owner)).status_code
        == 200
    )
    assert keeper.get(base(org) + "/settings").json() == body
    bad = owner.put(
        base(org) + "/settings", json={"negative_stock_policy": "x"}, headers=csrf_headers(owner)
    )
    assert bad.status_code == 422


# ---------------- основной сценарий ----------------


def test_receipt_issue_transfer_balances_movements_audit(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    w1, w2 = warehouse(owner, org, "Склад 1"), warehouse(owner, org, "Склад 2")
    milk, bread = product(owner, org, "MILK"), product(owner, org, "BREAD", unit_code="kg")

    r = do(
        owner, org, "receipt", w1, [(milk, "10"), (bread, "2.5"), (milk, "0.5")], reference="ПН-1"
    )
    assert r["status"] == "POSTED" and r["version"] == 2 and r["posted_by_name"]
    assert [ln["quantity"] for ln in r["lines"]] == ["10", "2.5", "0.5"]
    assert qty(owner, org, w1, milk) == "10.5" and qty(owner, org, w1, bread) == "2.5"

    do(owner, org, "issue", w1, [(milk, "3")])
    do(owner, org, "transfer", w1, [(milk, "4"), (bread, "1")], to_warehouse_id=w2)
    assert qty(owner, org, w1, milk) == "3.5" and qty(owner, org, w2, milk) == "4"
    assert qty(owner, org, w1, bread) == "1.5" and qty(owner, org, w2, bread) == "1"

    mv = owner.get(base(org) + "/movements", params={"product_id": milk, "page_size": 100}).json()
    assert mv["total"] == 5  # 2 прихода + расход + 2 ноги перемещения
    assert sorted(m["quantity_delta"] for m in mv["items"]) == ["-3", "-4", "0.5", "10", "4"]
    assert all(m["unit"]["code"] == "pcs" and m["sku"] == "MILK" for m in mv["items"])
    assert reconciliation(owner, org) == {"ok": True, "checked_pairs": 4, "mismatches": []}
    assert actions(admin_engine).count("stock_operation.posted") == 3
    assert "stock_operation.created" in actions(admin_engine)


def test_issue_beyond_stock_is_rejected_atomically(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    a, b = product(owner, org, "A"), product(owner, org, "B")
    do(owner, org, "receipt", wid, [(a, "5"), (b, "5")])
    d = create(owner, org, op_body("issue", wid, [(a, "2"), (b, "9")])).json()
    r = post_op(owner, org, d["id"])
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    det = r.json()["details"][0]["params"]
    assert (det["sku"], det["available"], det["required"]) == ("B", "5", "9")
    # ничего не изменилось: ни остатков, ни движений; операция осталась черновиком
    assert qty(owner, org, wid, a) == "5" and qty(owner, org, wid, b) == "5"
    assert owner.get(f"{base(org)}/operations/{d['id']}").json()["status"] == "DRAFT"
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM stock_movement")).scalar_one() == 2
    assert reconciliation(owner, org)["ok"] is True


def test_same_product_in_several_lines_is_checked_in_total(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "5")])
    d = create(
        owner, org, op_body("issue", wid, [(p, "3"), (p, "3")])
    ).json()  # по отдельности можно, вместе нет
    assert post_op(owner, org, d["id"]).status_code == 409
    assert qty(owner, org, wid, p) == "5"


def test_negative_stock_allowed_only_by_org_policy(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    d = create(owner, org, op_body("issue", wid, [(p, "2")])).json()
    assert post_op(owner, org, d["id"]).status_code == 409
    owner.put(
        base(org) + "/settings",
        json={"negative_stock_policy": "allow"},
        headers=csrf_headers(owner),
    )
    assert post_op(owner, org, d["id"]).status_code == 200
    assert qty(owner, org, wid, p) == "-2"
    assert reconciliation(owner, org)["ok"] is True


def test_transfer_needs_stock_and_distinct_warehouses(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    w1, w2 = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    p = product(owner, org, "P")
    same = create(owner, org, op_body("transfer", w1, [(p, "1")], to_warehouse_id=w1))
    assert same.status_code == 422 and same.json()["code"] == "warehouses_must_differ"
    no_to = create(owner, org, op_body("transfer", w1, [(p, "1")]))
    assert no_to.json()["code"] == "to_warehouse_required"
    extra = create(owner, org, op_body("receipt", w1, [(p, "1")], to_warehouse_id=w2))
    assert extra.json()["code"] == "to_warehouse_not_allowed"
    d = create(owner, org, op_body("transfer", w1, [(p, "1")], to_warehouse_id=w2)).json()
    assert post_op(owner, org, d["id"]).status_code == 409
    assert qty(owner, org, w1, p) == "0" and qty(owner, org, w2, p) == "0"


# ---------------- повторное проведение и гонки ----------------


def test_double_post_is_blocked(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    d = create(owner, org, op_body("receipt", wid, [(p, "4")])).json()
    assert post_op(owner, org, d["id"]).status_code == 200
    again = post_op(owner, org, d["id"], 2)
    assert again.status_code == 409 and again.json()["code"] == "operation_already_posted"
    assert qty(owner, org, wid, p) == "4"


def test_concurrent_posts_of_one_operation_apply_once(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    d = create(owner, org, op_body("receipt", wid, [(p, "7")])).json()
    barrier = threading.Barrier(5)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            r = c.post(
                f"{base(org)}/operations/{d['id']}/post", json={"expected_version": 1}, headers=h
            )
            out.append(r.status_code)

    ts = [threading.Thread(target=worker) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [200, 409, 409, 409, 409], out
    assert qty(owner, org, wid, p) == "7" and reconciliation(owner, org)["ok"]


def test_concurrent_issues_never_go_negative(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    drafts = [create(owner, org, op_body("issue", wid, [(p, "1")])).json()["id"] for _ in range(16)]
    barrier = threading.Barrier(8)
    out: list[int] = []
    lock = threading.Lock()

    def worker(chunk: list[str]) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            for oid in chunk:
                r = c.post(
                    f"{base(org)}/operations/{oid}/post", json={"expected_version": 1}, headers=h
                )
                with lock:
                    out.append(r.status_code)

    ts = [threading.Thread(target=worker, args=(drafts[i::8],)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out.count(200) == 10 and out.count(409) == 6, out
    assert qty(owner, org, wid, p) == "0"
    assert reconciliation(owner, org)["ok"] is True
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM stock_movement WHERE quantity_delta < 0")
            ).scalar_one()
            == 10
        )


def test_concurrent_opposite_transfers_do_not_deadlock(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    w1, w2 = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    p = product(owner, org, "P")
    do(owner, org, "receipt", w1, [(p, "20")])
    do(owner, org, "receipt", w2, [(p, "20")])
    ops = [
        create(
            owner,
            org,
            op_body(
                "transfer",
                w1 if i % 2 == 0 else w2,
                [(p, "1")],
                to_warehouse_id=w2 if i % 2 == 0 else w1,
            ),
        ).json()["id"]
        for i in range(12)
    ]
    barrier = threading.Barrier(6)
    out: list[int] = []
    lock = threading.Lock()

    def worker(chunk: list[str]) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            for oid in chunk:
                r = c.post(
                    f"{base(org)}/operations/{oid}/post", json={"expected_version": 1}, headers=h
                )
                with lock:
                    out.append(r.status_code)

    ts = [threading.Thread(target=worker, args=(ops[i::6],)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out == [200] * 12, out
    assert qty(owner, org, w1, p) == "20" and qty(owner, org, w2, p) == "20"  # 6 туда, 6 обратно
    assert reconciliation(owner, org)["ok"] is True


# ---------------- проверки ввода ----------------


def test_validation_of_lines_and_references(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    goods = product(owner, org, "G")
    svc = product(owner, org, "S", kind="service")
    other, org2 = setup_org(new_client, "two@example.uz")
    foreign_p = product(other, org2, "F")
    foreign_w = warehouse(other, org2)

    def code(body: dict[str, Any]) -> Any:
        r = create(owner, org, body)
        return r.status_code, r.json()["code"]

    assert code(op_body("receipt", wid, [(svc, "1")])) == (422, "product_not_goods")
    assert code(op_body("receipt", wid, [(foreign_p, "1")])) == (422, "product_not_found")
    assert code(op_body("receipt", foreign_w, [(goods, "1")])) == (422, "warehouse_not_found")
    for bad in ("0", "-1", "1.1234567", "abc", "1e3", "100000000000000"):
        assert create(owner, org, op_body("receipt", wid, [(goods, bad)])).status_code == 422, bad
    float_body = op_body("receipt", wid, [(goods, "1")])
    float_body["lines"][0]["quantity"] = 1.5  # float в JSON-числе недопустим
    assert create(owner, org, float_body).status_code == 422
    assert (
        create(owner, org, {**op_body("receipt", wid, [(goods, "1")]), "lines": []}).status_code
        == 422
    )
    assert (
        create(
            owner, org, {**op_body("receipt", wid, [(goods, "1")]), "kind": "count_adjustment"}
        ).status_code
        == 422
    )
    # архивные склад и товар
    owner.post(api(org, f"/products/{goods}/archive"), headers=csrf_headers(owner))
    assert code(op_body("receipt", wid, [(goods, "1")])) == (422, "product_archived")
    p2 = product(owner, org, "G2")
    owner.post(api(org, f"/warehouses/{wid}/archive"), headers=csrf_headers(owner))
    assert code(op_body("receipt", wid, [(p2, "1")])) == (422, "warehouse_archived")


def test_post_revalidates_archived_references(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    d = create(owner, org, op_body("receipt", wid, [(p, "1")])).json()
    owner.post(api(org, f"/products/{p}/archive"), headers=csrf_headers(owner))
    r = post_op(owner, org, d["id"])
    assert r.status_code == 422 and r.json()["code"] == "product_archived"
    assert qty(owner, org, wid, p) == "0"


def test_six_decimals_are_exact(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "0.000001"), (p, "1000.1"), (p, "0.2")])
    assert qty(owner, org, wid, p) == "1000.300001"
    do(owner, org, "issue", wid, [(p, "0.300001")])
    assert qty(owner, org, wid, p) == "1000"


# ---------------- черновики ----------------


def test_draft_update_conflict_delete_and_posted_is_frozen(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    w1, w2 = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    p, q = product(owner, org, "P"), product(owner, org, "Q")
    d = create(owner, org, op_body("receipt", w1, [(p, "1")])).json()
    url = f"{base(org)}/operations/{d['id']}"
    h = csrf_headers(owner)
    r = owner.patch(
        url,
        json={
            "expected_version": 1,
            "reference": "X-1",
            "lines": [{"product_id": q, "quantity": "7"}],
        },
        headers=h,
    )
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["lines"][0]["sku"] == "Q"
    stale = owner.patch(url, json={"expected_version": 1, "notes": "n"}, headers=h)
    assert stale.status_code == 409 and stale.json()["code"] == "operation_conflict"
    # смена склада без строк пересматривает ссылки; для прихода склад назначения недопустим
    bad = owner.patch(url, json={"expected_version": 2, "to_warehouse_id": w2}, headers=h)
    assert bad.status_code == 422
    ok = owner.patch(
        url, json={"expected_version": 2, "warehouse_id": w2, "notes": "переезд"}, headers=h
    )
    assert ok.status_code == 200 and ok.json()["warehouse"]["id"] == w2
    assert post_op(owner, org, d["id"]).status_code == 200
    frozen = owner.patch(url, json={"expected_version": 4, "notes": "поздно"}, headers=h)
    assert frozen.status_code == 409 and frozen.json()["code"] == "operation_not_draft"
    assert owner.delete(url, params={"expected_version": 4}, headers=h).status_code == 409
    d2 = create(owner, org, op_body("receipt", w1, [(p, "1")])).json()
    assert (
        owner.delete(
            f"{base(org)}/operations/{d2['id']}", params={"expected_version": 1}, headers=h
        ).status_code
        == 204
    )
    assert owner.get(f"{base(org)}/operations/{d2['id']}").status_code == 404
    acts = actions(admin_engine)
    assert "stock_operation.updated" in acts and "stock_operation.deleted" in acts


# ---------------- склад с остатком ----------------


def test_warehouse_with_stock_cannot_be_archived(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid, empty = warehouse(owner, org, "Полный"), warehouse(owner, org, "Пустой")
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "3")])
    h = csrf_headers(owner)
    r = owner.post(api(org, f"/warehouses/{wid}/archive"), headers=h)
    assert r.status_code == 409 and r.json()["code"] == "warehouse_has_stock"
    assert owner.post(api(org, f"/warehouses/{empty}/archive"), headers=h).status_code == 200
    do(owner, org, "issue", wid, [(p, "3")])
    assert owner.post(api(org, f"/warehouses/{wid}/archive"), headers=h).status_code == 200


# ---------------- списки ----------------


def test_balances_filters_search_sort_pagination(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    w1, w2 = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    ps = [product(owner, org, f"SKU{i:02d}") for i in range(6)]
    do(owner, org, "receipt", w1, [(p, str(i + 1)) for i, p in enumerate(ps)])
    do(owner, org, "receipt", w2, [(ps[0], "9")])
    do(owner, org, "issue", w1, [(ps[5], "6")])  # обнулён
    assert len(balances(owner, org)) == 6  # без нулевых
    assert len(balances(owner, org, include_zero=True)) == 7
    assert [b["sku"] for b in balances(owner, org, warehouse_id=w2)] == ["SKU00"]
    assert [b["sku"] for b in balances(owner, org, q="sku03")] == ["SKU03"]
    top = balances(owner, org, sort="quantity", order="desc", page_size=2)
    assert [b["quantity"] for b in top] == ["9", "5"]
    page2 = owner.get(base(org) + "/balances", params={"page": 2, "page_size": 4}).json()
    assert page2["total"] == 6 and len(page2["items"]) == 2
    assert owner.get(base(org) + "/balances", params={"sort": "evil"}).status_code == 422
    ops = owner.get(base(org) + "/operations", params={"status": "POSTED", "kind": "issue"}).json()
    assert ops["total"] == 1 and ops["items"][0]["lines_count"] == 1
    mv = owner.get(base(org) + "/movements", params={"warehouse_id": w2}).json()
    assert mv["total"] == 1


# ---------------- неизменяемость и сверка ----------------


def test_movements_are_append_only_even_for_db_owner(
    new_client: NewClient, admin_engine: Engine, app_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "3")])
    with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
        c.execute(text("UPDATE stock_movement SET quantity_delta = 99"))
    with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
        c.execute(text("DELETE FROM stock_movement"))
    with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as c:
        c.execute(text("UPDATE stock_movement SET quantity_delta = 99"))
    with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as c:
        c.execute(text("DELETE FROM stock_movement"))


def test_reconciliation_detects_tampered_balance(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "3")])
    assert reconciliation(owner, org)["ok"] is True
    with admin_engine.begin() as c:
        c.execute(text("UPDATE stock_balance SET quantity = 4"))
    rec = reconciliation(owner, org)
    assert rec["ok"] is False and len(rec["mismatches"]) == 1
    m = rec["mismatches"][0]
    assert (m["sku"], m["balance"], m["movements_sum"]) == ("P", "4", "3")


def test_posted_operation_cannot_be_deleted_at_db_level_with_movements(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    r = do(owner, org, "receipt", wid, [(p, "3")])
    with pytest.raises(DBAPIError), admin_engine.begin() as c:  # движения ссылаются на операцию
        c.execute(text("DELETE FROM stock_operation WHERE id = :i"), {"i": r["id"]})


# ---------------- изоляция организаций ----------------


def test_cross_tenant_isolation(new_client: NewClient, app_engine: Engine) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    wa, wb = warehouse(a, org_a), warehouse(b, org_b)
    pa, pb = product(a, org_a, "PA"), product(b, org_b, "PB")
    ra = do(a, org_a, "receipt", wa, [(pa, "5")])
    do(b, org_b, "receipt", wb, [(pb, "8")])
    # B не видит и не трогает операции A, по обоим адресам организаций
    assert b.get(f"{base(org_b)}/operations/{ra['id']}").status_code == 404
    assert b.get(f"{base(org_a)}/operations/{ra['id']}").status_code == 404
    assert b.get(base(org_a) + "/balances").status_code == 404
    assert post_op(b, org_b, ra["id"], 1).status_code == 404
    # чужие склад и товар нельзя использовать
    assert create(b, org_b, op_body("receipt", wa, [(pb, "1")])).status_code == 422
    assert create(b, org_b, op_body("receipt", wb, [(pa, "1")])).status_code == 422
    assert [x["sku"] for x in balances(b, org_b)] == ["PB"]
    # RLS на уровне БД: без контекста — пусто, с контекстом A — только A
    with app_engine.connect() as c:
        for t in ("stock_operation", "stock_operation_line", "stock_movement", "stock_balance"):
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for t in ("stock_operation", "stock_movement", "stock_balance"):
            orgs = {r[0] for r in c.execute(text(f"SELECT organization_id::text FROM {t}"))}
            assert orgs == {org_a}, t
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO stock_balance (organization_id, warehouse_id, product_id, quantity) "
                    "VALUES (:o, :w, :p, 1)"
                ),
                {"o": org_b, "w": wb, "p": pb},
            )
