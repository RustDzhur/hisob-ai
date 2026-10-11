"""Срез 6.2: сверка банковских операций с документами — кандидаты с объяснением, подтверждение, разбиение, объединение."""

import threading
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.files import pdf
from tests.helpers import csrf_headers, join, setup_org, upload_doc
from tests.integration.test_bank import (
    NewClient,
    actions,
    base,
    h,
    statement,
    txns,
    upload,
    with_profile,
)

_n = 0


def api(org: str, path: str) -> str:
    return f"/api/v1/organizations/{org}{path}"


def make_doc(
    c: TestClient,
    org: str,
    total: str,
    *,
    number: str | None = None,
    cp: str | None = None,
    date_: str = "2026-10-01",
    approve: bool = True,
    currency: str = "UZS",
) -> str:
    global _n
    _n += 1
    did: str = upload_doc(c, org, pdf(f"bm-{org}-{_n}")).json()["id"]
    cp_id = (
        cp
        or c.post(
            api(org, "/counterparties"),
            json={"name": f"Поставщик {_n}", "tin": str(400000000 + _n)},
            headers=csrf_headers(c),
        ).json()["id"]
    )
    body = {
        "expected_version": 1,
        "doc_type": "invoice",
        "number": number or f"СЧ-{_n}",
        "doc_date": date_,
        "counterparty_id": cp_id,
        "currency": currency,
        "total_amount": total,
        "lines": [{"description": "Услуга", "quantity": "1", "unit_price": total, "amount": total}],
    }
    r = c.patch(api(org, f"/documents/{did}"), json=body, headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    if approve:
        a = c.post(
            api(org, f"/documents/{did}/approve"),
            json={"expected_version": r.json()["version"], "acknowledged": []},
            headers=csrf_headers(c),
        )
        assert a.status_code == 200, a.text
    return did


def counterparty(c: TestClient, org: str, name: str, tin: str) -> str:
    r = c.post(
        api(org, "/counterparties"), json={"name": name, "tin": tin}, headers=csrf_headers(c)
    )
    assert r.status_code == 201, r.text
    cid: str = r.json()["id"]
    return cid


def load(
    c: TestClient, org: str, aid: str, *rows: str, name: str = "s.csv"
) -> list[dict[str, Any]]:
    r = upload(c, org, aid, statement(*rows), name=name)
    assert r.status_code == 201, r.text
    return txns(c, org, sort="txn_date", order="asc")


def detail(c: TestClient, org: str, tid: str) -> dict[str, Any]:
    r = c.get(base(org) + f"/transactions/{tid}")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def confirm(
    c: TestClient, org: str, tid: str, *allocs: tuple[str, str], note: str | None = None
) -> Any:
    return c.post(
        base(org) + f"/transactions/{tid}/matches",
        json={"allocations": [{"document_id": d, "amount": a} for d, a in allocs], "note": note},
        headers=h(c),
    )


def merge(c: TestClient, org: str, did: str, *allocs: tuple[str, str]) -> Any:
    return c.post(
        base(org) + "/matches/merge",
        json={
            "document_id": did,
            "allocations": [{"transaction_id": t, "amount": a} for t, a in allocs],
        },
        headers=h(c),
    )


def doc_bank(c: TestClient, org: str, did: str) -> dict[str, Any]:
    r = c.get(base(org) + f"/documents/{did}")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


# ---------------- кандидаты и объяснения ----------------


def test_candidates_are_explained_and_ranked(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    alpha = counterparty(owner, org, "ООО Альфа Трейд", "123456789")
    target = make_doc(owner, org, "1000", number="ТН-88", cp=alpha, date_="2026-10-01")
    same_amount = make_doc(owner, org, "1000", number="ДРУГОЙ-7")
    other_cp = make_doc(owner, org, "777", number="ЗЗЗ-1")
    items = load(
        owner,
        org,
        a["id"],
        "05.10.2026;1 000,00;Оплата по счёту TH-88 от 01.10;;ООО Альфа Трейд;123456789",
    )
    t = detail(
        owner, org, items[0]["id"]
    )  # TH-88 латиницей: буквы-двойники сравниваются без различий
    cands = t["candidates"]
    assert cands[0]["document"]["id"] == target
    codes = {r["code"] for r in cands[0]["reasons"]}
    assert codes == {"number_in_purpose", "amount_equal", "counterparty_tin", "date_window"}
    assert cands[0]["score"] == 40 + 40 + 30 + 10 and cands[0]["suggested_amount"] == "1000.00"
    assert (
        cands[0]["document"]["remaining"] == "1000.00"
        and cands[0]["document"]["counterparty_name"] == "ООО Альфа Трейд"
    )
    ids = [c["document"]["id"] for c in cands]
    assert (
        same_amount in ids and ids.index(same_amount) > 0
    )  # по одной сумме: ниже и с единственной причиной
    assert {r["code"] for r in cands[ids.index(same_amount)]["reasons"]} >= {"amount_equal"}
    assert other_cp not in ids  # слабее порога
    # неутверждённые документы и документы другой валюты не предлагаются
    draft = make_doc(owner, org, "1000", number="ТН-88Д", approve=False)
    assert draft not in [
        c["document"]["id"] for c in detail(owner, org, items[0]["id"])["candidates"]
    ]
    assert owner.get(base(org) + "/transactions").json()["items"][0]["remaining"] == "1000.00"


def test_reverse_candidates_for_document(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "500", number="АКТ-12")
    load(owner, org, a["id"], "02.10.2026;500,00;По акту АКТ-12;;;", "03.10.2026;9,99;Мелочь;;;")
    r = doc_bank(owner, org, d)
    assert (
        r["total"] == "500.00"
        and r["matched_total"] == "0.00"
        and r["remaining"] == "500.00"
        and r["matches"] == []
    )
    assert len(r["candidates"]) == 1
    c = r["candidates"][0]
    assert c["transaction"]["amount"] == "500.00" and {x["code"] for x in c["reasons"]} >= {
        "number_in_purpose",
        "amount_equal",
    }
    assert c["suggested_amount"] == "500.00" and c["transaction"]["bank_account_name"] == "Основной"


# ---------------- подтверждение, частичные оплаты, разбиение, объединение ----------------


def test_confirm_full_match_updates_statuses_and_keeps_explanation(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-1")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;Оплата СФ-1;;;")[0]
    r = confirm(owner, org, t["id"], (d, "1000"), note="проверено")
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["status"], out["matched_amount"], out["remaining"], out["candidates"]) == (
        "MATCHED",
        "1000.00",
        "0.00",
        [],
    )
    m = out["matches"][0]
    assert (m["document_number"], m["amount"], m["status"], m["note"], m["created_by_name"]) == (
        "СФ-1",
        "1000.00",
        "CONFIRMED",
        "проверено",
        "Test User",
    )
    assert {x["code"] for x in m["reasons"]} >= {
        "number_in_purpose",
        "amount_equal",
    }  # объяснение сохранено с сопоставлением
    assert (
        doc_bank(owner, org, d)["remaining"] == "0.00"
        and doc_bank(owner, org, d)["candidates"] == []
    )
    assert txns(owner, org, status="MATCHED")[0]["matched_amount"] == "1000.00"
    assert txns(owner, org, status="UNMATCHED") == []
    assert "bank_match.created" in actions(admin_engine)
    # полностью оплаченный документ больше не предлагается
    t2 = load(owner, org, a["id"], "06.10.2026;1 000,00;Ещё платёж СФ-1;;;", name="s2.csv")[-1]
    assert d not in [c["document"]["id"] for c in detail(owner, org, t2["id"])["candidates"]]


def test_partial_payments_and_merge(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-2")
    t1, t2, t3 = load(
        owner,
        org,
        a["id"],
        "01.10.2026;300,00;Аванс СФ-2;;;",
        "02.10.2026;500,00;Часть СФ-2;;;",
        "03.10.2026;400,00;Остаток СФ-2;;;",
    )
    assert confirm(owner, org, t1["id"], (d, "300")).json()["status"] == "MATCHED"
    b = doc_bank(owner, org, d)
    assert (b["matched_total"], b["remaining"]) == ("300.00", "700.00")
    cand = detail(owner, org, t2["id"])["candidates"][0]
    assert cand["document"]["remaining"] == "700.00" and "amount_partial" in {
        x["code"] for x in cand["reasons"]
    }
    # объединение: два платежа -> один документ атомарно; сумма больше остатка — отказ без записи
    over = merge(owner, org, d, (t2["id"], "500"), (t3["id"], "400"))
    assert over.status_code == 409 and over.json()["code"] == "amount_exceeds_document"
    assert over.json()["details"][0]["params"]["remaining"] == "700.00"
    assert detail(owner, org, t2["id"])["matched_amount"] == "0.00"  # ничего не записалось
    ok = merge(owner, org, d, (t2["id"], "500"), (t3["id"], "200"))
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert (body["matched_total"], body["remaining"]) == ("1000.00", "0.00") and len(
        body["matches"]
    ) == 3
    assert (
        detail(owner, org, t3["id"])["status"] == "UNMATCHED"
    )  # 400 платёж: 200 распределено, 200 свободно
    assert (
        detail(owner, org, t3["id"])["remaining"] == "200.00"
        and detail(owner, org, t2["id"])["status"] == "MATCHED"
    )


def test_split_one_payment_to_several_documents_and_validation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d1, d2 = make_doc(owner, org, "600", number="СФ-3"), make_doc(owner, org, "700", number="СФ-4")
    owner.put(api(org, "/currencies/USD"), json={"enabled": True}, headers=h(owner))
    usd = make_doc(owner, org, "100", number="USD-1", currency="USD")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;Оплата СФ-3 и СФ-4;;;")[0]

    def code(r: Any) -> tuple[int, str]:
        return r.status_code, r.json().get("code", "")

    assert code(confirm(owner, org, t["id"], (d1, "600"), (d2, "500"))) == (
        409,
        "amount_exceeds_transaction",
    )
    assert (
        confirm(owner, org, t["id"], (d1, "600"), (d2, "500")).json()["details"][0]["params"][
            "remaining"
        ]
        == "1000.00"
    )
    assert code(confirm(owner, org, t["id"], (d1, "700"))) == (409, "amount_exceeds_document")
    assert code(confirm(owner, org, t["id"], (d1, "100"), (d1, "100"))) == (
        422,
        "duplicate_allocation",
    )
    assert code(confirm(owner, org, t["id"], (usd, "10"))) == (422, "currency_mismatch")
    assert code(confirm(owner, org, t["id"], (d1, "10.001"))) == (422, "too_many_decimals")
    for bad in ("0", "-5", "1e3", "abc"):
        assert confirm(owner, org, t["id"], (d1, bad)).status_code == 422, bad
    draft = make_doc(owner, org, "100", number="ЧЕРН-1", approve=False)
    assert code(confirm(owner, org, t["id"], (draft, "10"))) == (409, "document_not_approved")
    assert confirm(owner, org, t["id"], (d1, "600"), (d2, "400")).status_code == 201
    s = detail(owner, org, t["id"])
    assert (s["status"], s["matched_amount"], s["remaining"], len(s["matches"])) == (
        "MATCHED",
        "1000.00",
        "0.00",
        2,
    )
    assert (doc_bank(owner, org, d1)["remaining"], doc_bank(owner, org, d2)["remaining"]) == (
        "0.00",
        "300.00",
    )
    assert code(confirm(owner, org, t["id"], (d2, "1"))) == (
        409,
        "amount_exceeds_transaction",
    )  # операция уже распределена


# ---------------- отмена, отклонение, пропуск ----------------


def test_cancel_and_rematch(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-5")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;СФ-5;;;")[0]
    mid = confirm(owner, org, t["id"], (d, "1000")).json()["matches"][0]["id"]
    out = owner.post(base(org) + f"/matches/{mid}/cancel", headers=h(owner))
    assert (
        out.status_code == 200
        and out.json()["status"] == "UNMATCHED"
        and out.json()["matched_amount"] == "0.00"
    )
    assert (
        out.json()["matches"][0]["status"] == "CANCELLED"
        and out.json()["matches"][0]["cancelled_at"]
    )
    assert out.json()["candidates"][0]["document"]["id"] == d  # снова предлагается
    assert (
        owner.post(base(org) + f"/matches/{mid}/cancel", headers=h(owner)).json()["code"]
        == "match_already_cancelled"
    )
    assert doc_bank(owner, org, d)["remaining"] == "1000.00"
    assert confirm(owner, org, t["id"], (d, "1000")).status_code == 201
    with pytest.raises(DBAPIError, match=r"cancelled|immutable"), admin_engine.begin() as c:
        c.execute(text(f"UPDATE bank_match SET amount = 1 WHERE id = '{mid}'"))
    with pytest.raises(DBAPIError, match=r"append-only"), admin_engine.begin() as c:
        c.execute(text("DELETE FROM bank_match"))
    assert "bank_match.cancelled" in actions(admin_engine)


def test_reject_candidate_and_ignore(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-6")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;СФ-6;;;")[0]
    rej = owner.post(
        base(org) + f"/transactions/{t['id']}/reject-candidate",
        json={"document_id": d},
        headers=h(owner),
    )
    assert rej.status_code == 200 and rej.json()["candidates"] == []
    assert (
        owner.post(
            base(org) + f"/transactions/{t['id']}/reject-candidate",
            json={"document_id": d},
            headers=h(owner),
        ).status_code
        == 200
    )  # идемпотентно
    assert doc_bank(owner, org, d)["candidates"] == []  # и со стороны документа
    assert (
        confirm(owner, org, t["id"], (d, "1000")).status_code == 201
    )  # отклонение не запрещает ручное решение
    assert "bank_match.candidate_rejected" in actions(admin_engine)
    # пропуск
    t2 = load(owner, org, a["id"], "06.10.2026;50,00;Не наше;;;", name="s2.csv")[-1]
    ig = owner.post(
        base(org) + f"/transactions/{t2['id']}/ignore",
        json={"reason": "личный платёж"},
        headers=h(owner),
    )
    assert ig.status_code == 200 and ig.json()["status"] == "IGNORED"
    assert confirm(owner, org, t2["id"], (d, "1")).json()["code"] == "transaction_ignored"
    assert len(txns(owner, org, status="IGNORED")) == 1
    assert (
        owner.post(base(org) + f"/transactions/{t2['id']}/unignore", headers=h(owner)).json()[
            "status"
        ]
        == "UNMATCHED"
    )
    assert (
        owner.post(base(org) + f"/transactions/{t['id']}/ignore", json={}, headers=h(owner)).json()[
            "code"
        ]
        == "transaction_has_matches"
    )
    assert {"bank_transaction.ignored", "bank_transaction.unignored"} <= set(actions(admin_engine))


def test_matched_document_cannot_be_reopened_or_archived(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-7")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;СФ-7;;;")[0]
    mid = confirm(owner, org, t["id"], (d, "1000")).json()["matches"][0]["id"]
    version = owner.get(api(org, f"/documents/{d}")).json()["version"]
    for action in ("reopen", "archive"):
        r = owner.post(
            api(org, f"/documents/{d}/{action}"),
            json={"expected_version": version},
            headers=h(owner),
        )
        assert r.status_code == 409 and r.json()["code"] == "document_has_bank_matches", action
    owner.post(base(org) + f"/matches/{mid}/cancel", headers=h(owner))
    assert (
        owner.post(
            api(org, f"/documents/{d}/reopen"), json={"expected_version": version}, headers=h(owner)
        ).status_code
        == 200
    )


# ---------------- права, гонки, база ----------------


def test_permissions_and_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-8")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;СФ-8;;;")[0]
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    accountant = join(new_client, owner, org, "acc@example.uz", "accountant")
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert auditor.get(base(org) + f"/transactions/{t['id']}").status_code == 200
    assert auditor.get(base(org) + f"/documents/{d}").status_code == 200
    assert confirm(auditor, org, t["id"], (d, "1000")).status_code == 403
    assert merge(auditor, org, d, (t["id"], "1")).status_code == 403
    assert (
        auditor.post(
            base(org) + f"/transactions/{t['id']}/ignore", json={}, headers=h(auditor)
        ).status_code
        == 403
    )
    assert keeper.get(base(org) + f"/transactions/{t['id']}").status_code == 403
    assert keeper.get(base(org) + f"/documents/{d}").status_code == 403
    assert confirm(accountant, org, t["id"], (d, "1000")).status_code == 201
    mid = detail(owner, org, t["id"])["matches"][0]["id"]
    assert auditor.post(base(org) + f"/matches/{mid}/cancel", headers=h(auditor)).status_code == 403
    other, org2 = setup_org(new_client, "two@example.uz")
    assert other.get(base(org2) + f"/transactions/{t['id']}").status_code == 404
    assert other.get(base(org) + f"/transactions/{t['id']}").status_code == 404
    assert confirm(other, org2, t["id"], (d, "1")).status_code == 404
    assert other.post(base(org2) + f"/matches/{mid}/cancel", headers=h(other)).status_code == 404
    b = with_profile(other, org2)
    t_other = load(other, org2, b["id"], "05.10.2026;5,00;x;;;")[0]
    assert confirm(other, org2, t_other["id"], (d, "1")).status_code == 404  # чужой документ


def test_concurrent_confirmations_never_overallocate(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    a = with_profile(owner, org)
    d = make_doc(owner, org, "1000", number="СФ-9")
    d2 = make_doc(owner, org, "1000", number="СФ-10")
    t = load(owner, org, a["id"], "05.10.2026;1 000,00;платёж;;;")[0]
    barrier = threading.Barrier(4)
    out: list[int] = []
    lock = threading.Lock()

    def worker(doc_id: str) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org) + f"/transactions/{t['id']}/matches",
                json={"allocations": [{"document_id": doc_id, "amount": "1000"}]},
                headers=hh,
            )
            with lock:
                out.append(r.status_code)

    ts = [threading.Thread(target=worker, args=(x,)) for x in (d, d2, d, d2)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert sorted(out) == [201, 409, 409, 409], out
    s = detail(owner, org, t["id"])
    assert (s["matched_amount"], s["remaining"], len(s["matches"])) == ("1000.00", "0.00", 1)
    # несколько платежей на один документ одновременно: сумма не превышает итог
    d3 = make_doc(owner, org, "900", number="СФ-11")
    items = load(
        owner,
        org,
        a["id"],
        *[f"0{i}.10.2026;400,00;часть {i};;;" for i in range(1, 5)],
        name="p.csv",
    )
    pays = [x for x in items if x["description"].startswith("часть")]
    barrier2 = threading.Barrier(4)
    res: list[int] = []

    def worker2(tid: str) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier2.wait()
            r = c.post(
                base(org) + f"/transactions/{tid}/matches",
                json={"allocations": [{"document_id": d3, "amount": "400"}]},
                headers=hh,
            )
            with lock:
                res.append(r.status_code)

    ts2 = [threading.Thread(target=worker2, args=(p["id"],)) for p in pays]
    for x in ts2:
        x.start()
    for x in ts2:
        x.join()
    assert sorted(res) == [201, 201, 409, 409], res  # 400 + 400 = 800 ≤ 900; третий не помещается
    assert Decimal(doc_bank(owner, org, d3)["matched_total"]) == Decimal("800.00")


def test_database_guards_match_limits(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "100", number="СФ-12")
    t = load(owner, org, a["id"], "05.10.2026;60,00;платёж;;;")[0]
    with admin_engine.connect() as c:
        user = str(c.execute(text("SELECT created_by FROM document LIMIT 1")).scalar_one())
    tx_engine = admin_engine.execution_options(isolation_level="READ COMMITTED")
    ins = text(
        "INSERT INTO bank_match (id, organization_id, transaction_id, document_id, amount, created_by) "
        "VALUES (gen_random_uuid(), :o, :t, :d, :a, :u)"
    )
    with (
        pytest.raises(DBAPIError, match="exceeds the payment"),
        tx_engine.begin() as c,
    ):  # больше платежа
        c.execute(ins, {"o": org, "t": t["id"], "d": d, "a": 70, "u": user})
    small = make_doc(owner, org, "50", number="СФ-13")
    big_txn = load(owner, org, a["id"], "06.10.2026;80,00;большой платёж;;;", name="b.csv")[-1]
    with (
        pytest.raises(DBAPIError, match="exceeds the document"),
        tx_engine.begin() as c,
    ):  # больше итога документа
        c.execute(ins, {"o": org, "t": big_txn["id"], "d": small, "a": 60, "u": user})
    with pytest.raises(DBAPIError), tx_engine.begin() as c:
        c.execute(ins, {"o": org, "t": t["id"], "d": d, "a": 0, "u": user})


def test_rls_on_match_tables(new_client: NewClient, app_engine: Engine) -> None:
    a_owner, org_a = setup_org(new_client, "a@example.uz")
    b_owner, org_b = setup_org(new_client, "b@example.uz")
    for c, org in ((a_owner, org_a), (b_owner, org_b)):
        acc = with_profile(c, org)
        d = make_doc(c, org, "100", number="СФ-1")
        t = load(c, org, acc["id"], "05.10.2026;100,00;СФ-1;;;")[0]
        confirm(c, org, t["id"], (d, "100"))
        c.post(
            base(org) + f"/transactions/{t['id']}/reject-candidate",
            json={"document_id": d},
            headers=h(c),
        )
    tables = ("bank_match", "bank_match_rejection")
    with app_engine.connect() as conn:
        for tbl in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {tbl}")).scalar_one() == 0, tbl
    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for tbl in tables:
            assert {
                r[0] for r in conn.execute(text(f"SELECT organization_id::text FROM {tbl}"))
            } == {org_a}, tbl
