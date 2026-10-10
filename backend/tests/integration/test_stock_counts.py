"""Срез 4.3: инвентаризация, минимальный запас, резервы."""

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import csrf_headers, join, setup_org
from tests.integration.test_stock import (
    NewClient,
    actions,
    balances,
    base,
    create,
    do,
    op_body,
    post_op,
    product,
    qty,
    reconciliation,
    warehouse,
)
from tests.integration.test_stock_documents import reverse

# ---------------- помощники ----------------


def h(c: TestClient) -> dict[str, str]:
    return csrf_headers(c)


def new_count(c: TestClient, org: str, wid: str, **extra: Any) -> Any:
    return c.post(base(org) + "/counts", json={"warehouse_id": wid, **extra}, headers=h(c))


def get_count(c: TestClient, org: str, cid: str) -> dict[str, Any]:
    r = c.get(f"{base(org)}/counts/{cid}")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def set_counted(c: TestClient, org: str, cid: str, counted: dict[str, str | None]) -> Any:
    v = get_count(c, org, cid)["version"]
    return c.patch(
        f"{base(org)}/counts/{cid}",
        json={
            "expected_version": v,
            "lines": [{"product_id": p, "counted_quantity": q} for p, q in counted.items()],
        },
        headers=h(c),
    )


def post_count(c: TestClient, org: str, cid: str, version: int | None = None) -> Any:
    if version is None:
        version = get_count(c, org, cid)["version"]
    return c.post(
        f"{base(org)}/counts/{cid}/post", json={"expected_version": version}, headers=h(c)
    )


def line(count: dict[str, Any], sku: str) -> dict[str, Any]:
    return next(x for x in count["lines"] if x["sku"] == sku)


# ---------------- инвентаризация ----------------


def test_count_adjusts_by_movements_and_keeps_history(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    a, b, c3 = product(owner, org, "A"), product(owner, org, "B"), product(owner, org, "C")
    do(owner, org, "receipt", wid, [(a, "10"), (b, "5"), (c3, "7")])
    cnt = new_count(owner, org, wid, notes="годовая").json()
    assert cnt["status"] == "IN_PROGRESS" and len(cnt["lines"]) == 3
    assert [(x["sku"], x["expected_quantity"], x["counted_quantity"]) for x in cnt["lines"]] == [
        ("A", "10", None),
        ("B", "5", None),
        ("C", "7", None),
    ]
    # A: недостача 2, B: излишек 3, C: не пересчитывали
    r = set_counted(owner, org, cnt["id"], {a: "8", b: "8", c3: None})
    assert r.status_code == 200, r.text
    done = post_count(owner, org, cnt["id"])
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["status"] == "POSTED" and out["operation_id"] and out["posted_by_name"]
    assert (
        line(out, "A")["applied_delta"],
        line(out, "B")["applied_delta"],
        line(out, "C")["applied_delta"],
    ) == ("-2", "3", None)
    assert (qty(owner, org, wid, a), qty(owner, org, wid, b), qty(owner, org, wid, c3)) == (
        "8",
        "8",
        "7",
    )
    op = owner.get(f"{base(org)}/operations/{out['operation_id']}").json()
    assert op["kind"] == "count_adjustment" and op["status"] == "POSTED"
    assert sorted(x["quantity"] for x in op["lines"]) == ["-2", "3"]
    mv = owner.get(base(org) + "/movements", params={"product_id": a, "page_size": 100}).json()[
        "items"
    ]
    assert sorted(m["quantity_delta"] for m in mv) == ["-2", "10"]  # история приходa сохранена
    assert reconciliation(owner, org)["ok"]
    assert {"stock_count.created", "stock_count.posted"} <= set(actions(admin_engine))
    # проведённую инвентаризацию не изменить и не провести повторно
    assert (
        post_count(owner, org, cnt["id"], out["version"]).json()["code"] == "count_already_posted"
    )
    assert set_counted(owner, org, cnt["id"], {a: "1"}).json()["code"] == "count_not_in_progress"


def test_count_without_discrepancy_creates_no_operation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "4")])
    cnt = new_count(owner, org, wid).json()
    set_counted(owner, org, cnt["id"], {p: "4"})
    out = post_count(owner, org, cnt["id"]).json()
    assert (
        out["status"] == "POSTED"
        and out["operation_id"] is None
        and out["lines"][0]["applied_delta"] == "0"
    )
    ops = owner.get(base(org) + "/operations", params={"kind": "count_adjustment"}).json()
    assert ops["total"] == 0


def test_delta_is_taken_from_the_current_balance_not_the_snapshot(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    cnt = new_count(owner, org, wid).json()  # снимок: 10
    do(owner, org, "issue", wid, [(p, "3")])  # во время пересчёта ушло 3 -> 7
    set_counted(owner, org, cnt["id"], {p: "5"})
    out = post_count(owner, org, cnt["id"]).json()
    assert out["lines"][0]["expected_quantity"] == "10" and out["lines"][0]["applied_delta"] == "-2"
    assert qty(owner, org, wid, p) == "5"


def test_count_can_add_products_and_validates_lines(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    known, fresh = product(owner, org, "KNOWN"), product(owner, org, "FRESH")
    svc = product(owner, org, "SRV", kind="service")
    do(owner, org, "receipt", wid, [(known, "2")])
    cnt = new_count(owner, org, wid).json()
    r = set_counted(
        owner, org, cnt["id"], {known: "2", fresh: "6"}
    )  # найден товар, которого не было в учёте
    assert r.status_code == 200 and line(r.json(), "FRESH")["expected_quantity"] == "0"
    assert set_counted(owner, org, cnt["id"], {svc: "1"}).json()["code"] == "product_not_goods"
    assert set_counted(owner, org, cnt["id"], {known: "-1"}).status_code == 422
    assert set_counted(owner, org, cnt["id"], {known: "1.1234567"}).status_code == 422
    v = get_count(owner, org, cnt["id"])["version"]
    dup = owner.patch(
        f"{base(org)}/counts/{cnt['id']}",
        json={"expected_version": v, "lines": [{"product_id": known}, {"product_id": known}]},
        headers=h(owner),
    )
    assert dup.json()["code"] == "duplicate_product"
    stale = owner.patch(
        f"{base(org)}/counts/{cnt['id']}",
        json={"expected_version": 1, "notes": "x"},
        headers=h(owner),
    )
    assert stale.status_code == 409 and stale.json()["code"] == "count_conflict"
    set_counted(owner, org, cnt["id"], {known: "2", fresh: "6"})
    assert post_count(owner, org, cnt["id"]).status_code == 200
    assert qty(owner, org, wid, fresh) == "6"


def test_one_active_count_per_warehouse_and_concurrency(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid, other = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            out.append(
                c.post(base(org) + "/counts", json={"warehouse_id": wid}, headers=hh).status_code
            )

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out
    assert new_count(owner, org, wid).json()["code"] == "count_already_in_progress"
    assert new_count(owner, org, other).status_code == 201  # другой склад — можно
    first = owner.get(base(org) + "/counts", params={"status": "IN_PROGRESS"}).json()["items"]
    mine = next(x for x in first if x["warehouse_name"] == "A")
    assert post_count(owner, org, mine["id"]).status_code == 200
    assert new_count(owner, org, wid).status_code == 201  # после проведения — снова можно


def test_concurrent_posts_of_a_count_apply_once(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    cnt = new_count(owner, org, wid).json()
    set_counted(owner, org, cnt["id"], {p: "4"})
    v = get_count(owner, org, cnt["id"])["version"]
    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            out.append(
                c.post(
                    f"{base(org)}/counts/{cnt['id']}/post", json={"expected_version": v}, headers=hh
                ).status_code
            )

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [200, 409, 409, 409], out
    assert qty(owner, org, wid, p) == "4" and reconciliation(owner, org)["ok"]


def test_count_delete_permissions_and_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "3")])
    cnt = new_count(owner, org, wid).json()
    manager = join(
        new_client, owner, org, "m@example.uz", "sales_purchase_manager"
    )  # manage, без post
    auditor = join(new_client, owner, org, "a@example.uz", "auditor")
    assert set_counted(manager, org, cnt["id"], {p: "3"}).status_code == 200
    assert post_count(manager, org, cnt["id"]).status_code == 403
    assert new_count(auditor, org, wid).status_code == 403
    assert auditor.get(f"{base(org)}/counts/{cnt['id']}").status_code == 200
    other, org2 = setup_org(new_client, "two@example.uz")
    assert other.get(f"{base(org2)}/counts/{cnt['id']}").status_code == 404
    assert post_count(other, org2, cnt["id"], 1).status_code == 404
    v = get_count(owner, org, cnt["id"])["version"]
    assert (
        owner.delete(
            f"{base(org)}/counts/{cnt['id']}", params={"expected_version": v}, headers=h(owner)
        ).status_code
        == 204
    )
    assert owner.get(f"{base(org)}/counts/{cnt['id']}").status_code == 404


def test_count_adjustment_can_be_reversed_and_ignores_reservations(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    assert (
        owner.post(
            base(org) + "/reservations",
            json={"warehouse_id": wid, "product_id": p, "quantity": "6"},
            headers=h(owner),
        ).status_code
        == 201
    )
    cnt = new_count(owner, org, wid).json()
    set_counted(
        owner, org, cnt["id"], {p: "2"}
    )  # физически осталось 2, резерв 6: реальность важнее резерва
    out = post_count(owner, org, cnt["id"]).json()
    assert qty(owner, org, wid, p) == "2"
    rv = reverse(owner, org, out["operation_id"])
    assert rv.status_code == 200, rv.text
    assert qty(owner, org, wid, p) == "10"
    assert reconciliation(owner, org)["ok"]


def test_adjustment_lines_are_signed_only_for_adjustments_in_db(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    op = create(owner, org, op_body("receipt", wid, [(p, "1")])).json()
    with (
        pytest.raises(DBAPIError),
        admin_engine.begin() as c,
    ):  # отрицательная строка обычной операции
        c.execute(
            text("UPDATE stock_operation_line SET quantity = -1 WHERE operation_id = :i"),
            {"i": op["id"]},
        )
    with pytest.raises(DBAPIError), admin_engine.begin() as c:  # нулевая строка
        c.execute(
            text(
                "UPDATE stock_operation_line SET quantity = 0, is_adjustment = true WHERE operation_id = :i"
            ),
            {"i": op["id"]},
        )


# ---------------- минимальный запас ----------------


def put_min(c: TestClient, org: str, wid: str, pid: str, value: str | None) -> Any:
    return c.put(
        base(org) + "/min-levels",
        json={"warehouse_id": wid, "product_id": pid, "min_quantity": value},
        headers=h(c),
    )


def test_min_levels_flag_low_stock(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    a, b, c3 = product(owner, org, "A"), product(owner, org, "B"), product(owner, org, "C")
    do(owner, org, "receipt", wid, [(a, "10"), (b, "10")])
    assert put_min(owner, org, wid, a, "5").status_code == 204
    assert put_min(owner, org, wid, b, "10").status_code == 204  # ровно на пороге — не низкий
    assert (
        put_min(owner, org, wid, c3, "3").status_code == 204
    )  # нет остатка вообще -> позиция появляется, низкий
    by = {x["sku"]: x for x in balances(owner, org, include_zero=True)}
    assert (by["A"]["min_quantity"], by["A"]["low"]) == ("5", False)
    assert (by["B"]["min_quantity"], by["B"]["low"]) == ("10", False)
    assert (by["C"]["quantity"], by["C"]["low"]) == ("0", True)
    do(owner, org, "issue", wid, [(a, "6")])  # 4 < 5
    low = balances(owner, org, low_only=True)
    assert sorted(x["sku"] for x in low) == ["A", "C"]
    # снять порог
    assert put_min(owner, org, wid, a, None).status_code == 204
    assert sorted(x["sku"] for x in balances(owner, org, low_only=True)) == ["C"]
    # проверки ввода
    assert put_min(owner, org, wid, a, "0").status_code == 422
    assert put_min(owner, org, wid, a, "-1").status_code == 422
    assert (
        put_min(owner, org, wid, product(owner, org, "SRV", kind="service"), "1").status_code == 422
    )
    assert "stock.min_level_set" in actions(admin_engine)


def test_min_level_permissions_and_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    auditor = join(new_client, owner, org, "a@example.uz", "auditor")
    assert put_min(keeper, org, wid, p, "2").status_code == 204
    assert put_min(auditor, org, wid, p, "3").status_code == 403
    other, org2 = setup_org(new_client, "two@example.uz")
    assert put_min(other, org2, wid, p, "1").status_code == 422  # чужие склад и товар
    assert put_min(other, org, wid, p, "1").status_code == 404


# ---------------- резервы ----------------


def reserve(c: TestClient, org: str, wid: str, pid: str, q: str, reason: str | None = None) -> Any:
    return c.post(
        base(org) + "/reservations",
        json={"warehouse_id": wid, "product_id": pid, "quantity": q, "reason": reason},
        headers=h(c),
    )


def release(c: TestClient, org: str, rid: str) -> Any:
    return c.post(f"{base(org)}/reservations/{rid}/release", headers=h(c))


def test_reservation_reduces_available_but_not_physical_stock(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    r = reserve(owner, org, wid, p, "6", "заказ №5")
    assert r.status_code == 201, r.text
    row = balances(owner, org)[0]
    assert (row["quantity"], row["reserved"], row["available"]) == ("10", "6", "4")
    over = reserve(owner, org, wid, p, "5")
    assert over.status_code == 409 and over.json()["code"] == "insufficient_available"
    assert over.json()["details"][0]["params"] == {"available": "4", "required": "5"}
    assert reserve(owner, org, wid, p, "4").status_code == 201
    assert balances(owner, org)[0]["available"] == "0"
    assert reconciliation(owner, org)[
        "ok"
    ]  # физический остаток = движения, резервы на сверку не влияют
    lst = owner.get(base(org) + "/reservations").json()
    assert lst["total"] == 2 and {x["reason"] for x in lst["items"]} == {"заказ №5", None}
    assert {"stock_reservation.created"} <= set(actions(admin_engine))


def test_posting_cannot_take_reserved_stock_until_released(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    w1, w2 = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    p = product(owner, org, "P")
    do(owner, org, "receipt", w1, [(p, "10")])
    rid = reserve(owner, org, w1, p, "6").json()["id"]
    blocked = create(owner, org, op_body("issue", w1, [(p, "5")])).json()
    r = post_op(owner, org, blocked["id"])
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    d = r.json()["details"][0]["params"]
    assert (d["available"], d["required"], d["reserved"]) == ("4", "5", "6")
    tr = create(owner, org, op_body("transfer", w1, [(p, "5")], to_warehouse_id=w2)).json()
    assert post_op(owner, org, tr["id"]).status_code == 409
    do(owner, org, "issue", w1, [(p, "4")])  # свободная часть — можно
    assert qty(owner, org, w1, p) == "6"
    # сторно прихода тоже уменьшает остаток и упирается в резерв
    rec = owner.get(base(org) + "/operations", params={"kind": "receipt"}).json()["items"][0]
    assert reverse(owner, org, rec["id"]).status_code == 409
    # снятие резерва освобождает остаток
    assert release(owner, org, rid).status_code == 200
    assert post_op(owner, org, blocked["id"]).status_code == 200  # остаток 6, расход 5
    assert qty(owner, org, w1, p) == "1"


def test_released_reservation_unblocks_posting(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    rid = reserve(owner, org, wid, p, "8").json()["id"]
    d = create(owner, org, op_body("issue", wid, [(p, "5")])).json()
    assert post_op(owner, org, d["id"]).status_code == 409
    assert release(owner, org, rid).status_code == 200
    assert post_op(owner, org, d["id"]).status_code == 200
    assert qty(owner, org, wid, p) == "5"
    assert release(owner, org, rid).json()["code"] == "reservation_not_active"
    assert (
        owner.get(base(org) + "/reservations", params={"status": "RELEASED"}).json()["total"] == 1
    )
    assert owner.get(base(org) + "/reservations").json()["total"] == 0


def test_negative_policy_allow_does_not_apply_reservations_to_posting(
    new_client: NewClient,
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    reserve(owner, org, wid, p, "8")
    owner.put(base(org) + "/settings", json={"negative_stock_policy": "allow"}, headers=h(owner))
    do(owner, org, "issue", wid, [(p, "9")])  # осознанное разрешение владельца
    assert qty(owner, org, wid, p) == "1"


def test_concurrent_reservations_never_exceed_stock(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    barrier = threading.Barrier(8)
    out: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org) + "/reservations",
                json={"warehouse_id": wid, "product_id": p, "quantity": "3"},
                headers=hh,
            )
            with lock:
                out.append(r.status_code)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out.count(201) == 3 and out.count(409) == 5, out
    assert balances(owner, org)[0]["available"] == "1"


def test_concurrent_reserve_and_issue_never_oversell(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    drafts = [create(owner, org, op_body("issue", wid, [(p, "4")])).json()["id"] for _ in range(3)]
    barrier = threading.Barrier(4)
    res: list[tuple[str, int]] = []
    lock = threading.Lock()

    def reserver() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org) + "/reservations",
                json={"warehouse_id": wid, "product_id": p, "quantity": "5"},
                headers=hh,
            )
            with lock:
                res.append(("reserve", r.status_code))

    def issuer(oid: str) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(
                f"{base(org)}/operations/{oid}/post", json={"expected_version": 1}, headers=hh
            )
            with lock:
                res.append(("issue", r.status_code))

    ts = [threading.Thread(target=reserver)] + [
        threading.Thread(target=issuer, args=(d,)) for d in drafts
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    row = balances(owner, org, include_zero=True)[0]
    # инвариант при любом порядке гонки: резерв не превышает физический остаток
    assert float(row["reserved"]) <= float(row["quantity"]), (row, res)
    assert float(row["quantity"]) >= 0
    assert reconciliation(owner, org)["ok"]


def test_reservation_validation_permissions_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "5")])
    for bad in ("0", "-1", "1.1234567", "abc"):
        assert reserve(owner, org, wid, p, bad).status_code == 422, bad
    assert (
        reserve(owner, org, wid, product(owner, org, "SRV", kind="service"), "1").status_code == 422
    )
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    auditor = join(new_client, owner, org, "a@example.uz", "auditor")
    rid = reserve(keeper, org, wid, p, "1").json()["id"]
    assert reserve(auditor, org, wid, p, "1").status_code == 403
    assert release(auditor, org, rid).status_code == 403
    assert auditor.get(base(org) + "/reservations").status_code == 200
    other, org2 = setup_org(new_client, "two@example.uz")
    assert release(other, org2, rid).status_code == 404
    assert other.get(base(org2) + "/reservations").json()["total"] == 0


def test_rls_on_new_tables(new_client: NewClient, app_engine: Engine) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    for c, org in ((a, org_a), (b, org_b)):
        wid = warehouse(c, org)
        p = product(c, org, "P")
        do(c, org, "receipt", wid, [(p, "5")])
        reserve(c, org, wid, p, "1")
        put_min(c, org, wid, p, "2")
        new_count(c, org, wid)
    tables = ("stock_count", "stock_count_line", "stock_min_level", "stock_reservation")
    with app_engine.connect() as conn:
        for t in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for t in tables:
            orgs = {r[0] for r in conn.execute(text(f"SELECT organization_id::text FROM {t}"))}
            assert orgs == {org_a}, t
