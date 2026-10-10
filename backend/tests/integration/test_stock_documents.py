"""Срез 4.2: операции по утверждённым документам, возвраты, сторно, блокировка смены единицы."""

import threading
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.files import pdf
from tests.helpers import csrf_headers, join, setup_org, upload_doc
from tests.integration.test_stock import (
    NewClient,
    actions,
    api,
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


def unit_id(c: TestClient, org: str, code: str) -> str:
    units = c.get(api(org, "/units")).json()
    return str(next(u["id"] for u in units if u["code"] == code))


def counterparty(c: TestClient, org: str) -> str:
    r = c.post(
        api(org, "/counterparties"),
        json={"name": "ООО Поставщик", "tin": "123456789"},
        headers=csrf_headers(c),
    )
    assert r.status_code == 201, r.text
    cid: str = r.json()["id"]
    return cid


_n = 0


def approved_doc(
    c: TestClient, org: str, lines: list[dict[str, Any]], *, approve: bool = True
) -> str:
    """Документ с заданными строками; цены/суммы согласованы, итог = сумма строк."""
    global _n
    _n += 1
    did: str = upload_doc(c, org, pdf(f"sd-{org}-{_n}")).json()["id"]
    cp = _cp.get(org) or counterparty(c, org)
    _cp[org] = cp
    total = sum(int(ln["amount"]) for ln in lines)
    body = {
        "expected_version": 1,
        "doc_type": "invoice",
        "number": f"НАК-{_n}",
        "doc_date": "2026-10-01",
        "counterparty_id": cp,
        "currency": "UZS",
        "total_amount": str(total),
        "lines": lines,
    }
    r = c.patch(api(org, f"/documents/{did}"), json=body, headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    if approve:
        v = r.json()["version"]
        a = c.post(
            api(org, f"/documents/{did}/approve"),
            json={"expected_version": v, "acknowledged": []},
            headers=csrf_headers(c),
        )
        assert a.status_code == 200, a.text
    return did


_cp: dict[str, str] = {}


def dline(
    desc: str, pid: str | None, uid: str | None, q: str, price: str = "100"
) -> dict[str, Any]:
    return {
        "description": desc,
        "product_id": pid,
        "unit_id": uid,
        "quantity": q,
        "unit_price": price,
        "amount": str(int(float(q) * int(price))),
    }


def from_doc(c: TestClient, org: str, doc: str, kind: str, wid: str) -> Any:
    return c.post(
        base(org) + "/operations/from-document",
        json={"document_id": doc, "kind": kind, "warehouse_id": wid},
        headers=csrf_headers(c),
    )


# ---------------- по документу ----------------


def test_receipt_from_approved_document_and_double_protection(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    milk, bread = product(owner, org, "MILK"), product(owner, org, "BREAD")
    pcs = unit_id(owner, org, "pcs")
    svc = product(owner, org, "DELIVERY", kind="service")
    doc = approved_doc(
        owner,
        org,
        [
            dline("Молоко", milk, pcs, "10"),
            dline("Хлеб", bread, pcs, "4"),
            dline("Доставка", svc, None, "1"),
        ],
    )
    r = from_doc(owner, org, doc, "receipt", wid)
    assert r.status_code == 201, r.text
    op = r.json()
    assert (
        op["status"] == "DRAFT" and op["kind"] == "receipt" and op["source_document"]["id"] == doc
    )
    assert op["reference"].startswith("НАК-") and op["op_date"] == "2026-10-01"
    assert [(ln["sku"], ln["quantity"]) for ln in op["lines"]] == [
        ("MILK", "10"),
        ("BREAD", "4"),
    ]  # услуга отсечена
    assert balances(owner, org) == []  # сам не проводится
    assert post_op(owner, org, op["id"]).status_code == 200
    assert qty(owner, org, wid, milk) == "10"
    # тот же вид по тому же документу — нельзя, другой вид — можно
    dup = from_doc(owner, org, doc, "receipt", wid)
    assert dup.status_code == 409 and dup.json()["code"] == "document_already_in_stock"
    assert from_doc(owner, org, doc, "customer_return", wid).status_code == 201
    # список по документу
    lst = owner.get(base(org) + "/operations", params={"document_id": doc}).json()
    assert lst["total"] == 2
    assert "stock_operation.created_from_document" in actions(admin_engine)


def test_document_must_be_approved_and_lines_ready(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    milk = product(owner, org, "MILK")
    kg, pcs = unit_id(owner, org, "kg"), unit_id(owner, org, "pcs")
    draft = approved_doc(owner, org, [dline("Молоко", milk, pcs, "1")], approve=False)
    r = from_doc(owner, org, draft, "receipt", wid)
    assert r.status_code == 409 and r.json()["code"] == "document_not_approved"

    bad = approved_doc(
        owner,
        org,
        [
            dline("Без товара", None, pcs, "1"),
            dline("Другая единица", milk, kg, "2"),
            dline("Нет кол-ва", milk, pcs, "0"),
        ],
    )
    r = from_doc(owner, org, bad, "receipt", wid)
    assert r.status_code == 422 and r.json()["code"] == "document_lines_not_ready"
    codes = {(d["code"], d["params"]["position"]) for d in r.json()["details"]}
    assert codes == {
        ("line_product_missing", 1),
        ("line_unit_mismatch", 2),
        ("line_quantity_missing", 3),
    }
    mism = next(d for d in r.json()["details"] if d["code"] == "line_unit_mismatch")
    assert mism["params"]["sku"] == "MILK"
    assert balances(owner, org) == []

    only_service = approved_doc(
        owner, org, [dline("Услуга", product(owner, org, "SRV", kind="service"), None, "1")]
    )
    r = from_doc(owner, org, only_service, "receipt", wid)
    assert r.status_code == 422 and r.json()["code"] == "no_stock_lines"


def test_unit_mismatch_is_resolved_by_changing_the_product_unit_until_stock_exists(
    new_client: NewClient,
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    flour = product(owner, org, "FLOUR", unit_code="pcs")  # заведена в штуках, в документе — кг
    kg = unit_id(owner, org, "kg")
    doc = approved_doc(owner, org, [dline("Мука", flour, kg, "25")])
    assert from_doc(owner, org, doc, "receipt", wid).json()["code"] == "document_lines_not_ready"
    # нет движений — единицу товара можно привести к документу
    ch = owner.patch(
        api(org, f"/products/{flour}"), json={"unit_id": kg}, headers=csrf_headers(owner)
    )
    assert ch.status_code == 200, ch.text
    r = from_doc(owner, org, doc, "receipt", wid)
    assert r.status_code == 201 and r.json()["lines"][0]["unit"]["code"] == "kg"
    assert post_op(owner, org, r.json()["id"]).status_code == 200
    # после движений единицу менять нельзя: количества потеряли бы смысл
    pcs = unit_id(owner, org, "pcs")
    locked = owner.patch(
        api(org, f"/products/{flour}"), json={"unit_id": pcs}, headers=csrf_headers(owner)
    )
    assert locked.status_code == 409 and locked.json()["code"] == "product_unit_locked"
    assert owner.get(api(org, f"/products/{flour}")).json()["unit"]["code"] == "kg"


def test_deleting_draft_frees_the_document(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid, milk = warehouse(owner, org), product(owner, org, "MILK")
    doc = approved_doc(owner, org, [dline("Молоко", milk, unit_id(owner, org, "pcs"), "3")])
    d = from_doc(owner, org, doc, "receipt", wid).json()
    assert from_doc(owner, org, doc, "receipt", wid).status_code == 409
    assert (
        owner.delete(
            f"{base(org)}/operations/{d['id']}",
            params={"expected_version": 1},
            headers=csrf_headers(owner),
        ).status_code
        == 204
    )
    assert from_doc(owner, org, doc, "receipt", wid).status_code == 201


def test_from_document_permissions_and_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid, milk = warehouse(owner, org), product(owner, org, "MILK")
    doc = approved_doc(owner, org, [dline("Молоко", milk, unit_id(owner, org, "pcs"), "3")])
    operator = join(
        new_client, owner, org, "op@example.uz", "document_operator"
    )  # stock.read, без manage
    assert from_doc(operator, org, doc, "receipt", wid).status_code == 403
    manager = join(new_client, owner, org, "m@example.uz", "sales_purchase_manager")
    assert from_doc(manager, org, doc, "issue", wid).status_code == 201
    other, org2 = setup_org(new_client, "two@example.uz")
    wid2 = warehouse(other, org2)
    assert from_doc(other, org2, doc, "receipt", wid2).status_code == 404  # чужой документ
    assert from_doc(other, org, doc, "receipt", wid).status_code == 404  # чужая организация


def test_concurrent_creation_from_one_document(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid, milk = warehouse(owner, org), product(owner, org, "MILK")
    doc = approved_doc(owner, org, [dline("Молоко", milk, unit_id(owner, org, "pcs"), "3")])
    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            out.append(
                c.post(
                    base(org) + "/operations/from-document",
                    json={"document_id": doc, "kind": "receipt", "warehouse_id": wid},
                    headers=h,
                ).status_code
            )

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out


# ---------------- возвраты ----------------


def test_returns_move_stock_in_the_expected_direction(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    do(owner, org, "supplier_return", wid, [(p, "3")])
    assert qty(owner, org, wid, p) == "7"
    do(owner, org, "customer_return", wid, [(p, "2")])
    assert qty(owner, org, wid, p) == "9"
    over = create(owner, org, op_body("supplier_return", wid, [(p, "50")])).json()
    assert post_op(owner, org, over["id"]).json()["code"] == "insufficient_stock"
    assert reconciliation(owner, org)["ok"]


# ---------------- сторно ----------------


def reverse(c: TestClient, org: str, oid: str, version: int | None = None, **extra: Any) -> Any:
    if version is None:
        version = c.get(f"{base(org)}/operations/{oid}").json()["version"]
    return c.post(
        f"{base(org)}/operations/{oid}/reverse",
        json={"expected_version": version, **extra},
        headers=csrf_headers(c),
    )


def test_reverse_restores_stock_and_keeps_history(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    rec = do(owner, org, "receipt", wid, [(p, "10")])
    iss = do(owner, org, "issue", wid, [(p, "4")])
    # приход 10 при остатке 6 сторнировать нельзя (ушло бы в минус)
    r = reverse(owner, org, rec["id"])
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    assert qty(owner, org, wid, p) == "6"
    ok = reverse(owner, org, iss["id"], op_date="2026-10-05")
    assert ok.status_code == 200, ok.text
    rv = ok.json()
    assert (
        rv["status"] == "POSTED"
        and rv["reverses_id"] == iss["id"]
        and rv["op_date"] == "2026-10-05"
    )
    assert qty(owner, org, wid, p) == "10"
    after = owner.get(f"{base(org)}/operations/{iss['id']}").json()
    assert after["reversed_by_id"] == rv["id"] and after["version"] == iss["version"] + 1
    # повторы и сторно сторно
    assert reverse(owner, org, iss["id"]).json()["code"] == "operation_already_reversed"
    assert reverse(owner, org, rv["id"]).json()["code"] == "operation_is_reversal"
    mv = owner.get(base(org) + "/movements", params={"product_id": p, "page_size": 100}).json()
    assert sorted(m["quantity_delta"] for m in mv["items"]) == [
        "-4",
        "10",
        "4",
    ]  # история не стёрта
    lst = owner.get(base(org) + "/operations", params={"page_size": 100}).json()["items"]
    assert {(o["id"], o["is_reversal"], o["reversed"]) for o in lst} >= {
        (rv["id"], True, False),
        (iss["id"], False, True),
    }
    assert reconciliation(owner, org)["ok"]
    assert "stock_operation.reversed" in actions(admin_engine)
    # теперь приход можно сторнировать целиком
    assert reverse(owner, org, rec["id"]).status_code == 200
    assert qty(owner, org, wid, p) == "0"


def test_reverse_requires_posted_version_and_permission(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    draft = create(owner, org, op_body("receipt", wid, [(p, "1")])).json()
    assert reverse(owner, org, draft["id"], 1).json()["code"] == "operation_not_posted"
    posted = do(owner, org, "receipt", wid, [(p, "5")])
    assert reverse(owner, org, posted["id"], 1).json()["code"] == "operation_conflict"
    manager = join(
        new_client, owner, org, "m@example.uz", "sales_purchase_manager"
    )  # без stock.post
    assert reverse(manager, org, posted["id"]).status_code == 403
    other, org2 = setup_org(new_client, "two@example.uz")
    assert reverse(other, org2, posted["id"], 2).status_code == 404


def test_reverse_transfer_and_document_receipt(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    w1, w2 = warehouse(owner, org, "A"), warehouse(owner, org, "B")
    p = product(owner, org, "P")
    do(owner, org, "receipt", w1, [(p, "10")])
    tr = do(owner, org, "transfer", w1, [(p, "6")], to_warehouse_id=w2)
    assert (qty(owner, org, w1, p), qty(owner, org, w2, p)) == ("4", "6")
    assert reverse(owner, org, tr["id"]).status_code == 200
    assert (qty(owner, org, w1, p), qty(owner, org, w2, p)) == ("10", "0")
    # после сторно оприходованного документа его можно оформить заново
    doc = approved_doc(owner, org, [dline("P", p, unit_id(owner, org, "pcs"), "5")])
    first = from_doc(owner, org, doc, "receipt", w1).json()
    posted = post_op(owner, org, first["id"]).json()
    assert from_doc(owner, org, doc, "receipt", w1).status_code == 409
    assert reverse(owner, org, posted["id"]).status_code == 200
    again = from_doc(owner, org, doc, "receipt", w1)
    assert again.status_code == 201
    assert reconciliation(owner, org)["ok"]


def test_concurrent_reversals_apply_once(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    do(owner, org, "receipt", wid, [(p, "10")])
    iss = do(owner, org, "issue", wid, [(p, "4")])
    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            out.append(
                c.post(
                    f"{base(org)}/operations/{iss['id']}/reverse",
                    json={"expected_version": iss["version"]},
                    headers=h,
                ).status_code
            )

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [200, 409, 409, 409], out
    assert qty(owner, org, wid, p) == "10" and reconciliation(owner, org)["ok"]


def test_db_rejects_second_reversal_and_reversal_of_reversal(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    wid = warehouse(owner, org)
    p = product(owner, org, "P")
    rec = do(owner, org, "receipt", wid, [(p, "3")])
    rv = reverse(owner, org, rec["id"]).json()
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), admin_engine.begin() as c:  # ещё одно сторно той же операции
        c.execute(
            text(
                "INSERT INTO stock_operation (id, organization_id, kind, status, op_date, warehouse_id, reverses_id, created_by, posted_by, posted_at) "
                "SELECT gen_random_uuid(), organization_id, kind, 'POSTED', op_date, warehouse_id, id, created_by, created_by, now() "
                "FROM stock_operation WHERE id = :i"
            ),
            {"i": rec["id"]},
        )
    assert rv["reverses_id"] == rec["id"]
