"""Срез 6.3: отчёты по банку — сводка, сопоставления, CSV; права, изоляция, защита от формул."""

from decimal import Decimal

from tests.helpers import join, setup_org
from tests.integration.test_bank import NewClient, base, h, with_profile
from tests.integration.test_bank_reconcile import confirm, counterparty, load, make_doc

Q = {"date_from": "2026-10-01", "date_to": "2026-10-31"}


def test_summary_counts_amounts_and_unmatched(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    d = make_doc(owner, org, "600", number="СФ-1")
    items = load(
        owner,
        org,
        a["id"],
        "15.09.2026;50,00;Ранее;;;",  # до периода
        "05.10.2026;1 000,00;Оплата СФ-1;;;",
        "06.10.2026;-250,50;Комиссия;;;",
        "07.10.2026;300,00;Прочее;;;",
        "08.11.2026;9,00;После периода;;;",
    )
    big = next(i for i in items if i["amount"] == "1000.00")
    assert confirm(owner, org, big["id"], (d, "600")).status_code == 201
    other = next(i for i in items if i["amount"] == "300.00")
    owner.post(
        base(org) + f"/transactions/{other['id']}/ignore",
        json={},
        headers=h(owner),
    )
    r = owner.get(base(org) + "/reports/summary", params=Q)
    assert r.status_code == 200, r.text
    (row,) = r.json()["rows"]
    assert row["currency"] == "UZS"
    assert row["opening"] == "50.00"
    assert row["incoming"] == "1300.00"
    assert row["outgoing"] == "250.50"
    assert row["net"] == "1049.50"
    assert row["closing"] == "1099.50"
    assert row["txn_count"] == 3
    assert (row["unmatched_count"], row["matched_count"], row["ignored_count"]) == (2, 0, 1)
    # не сопоставлено: (1000-600) + 250.50 = 650.50; пропущенная 300 не считается
    assert Decimal(row["unmatched_amount"]) == Decimal("650.50")
    # drill-down: фильтры списка дают те же количества
    for status, key in (("UNMATCHED", "unmatched_count"), ("IGNORED", "ignored_count")):
        lst = owner.get(
            base(org) + "/transactions",
            params={"status": status, "account_id": a["id"], **Q},
        ).json()
        assert lst["total"] == row[key]


def test_summary_validation_and_account_filter(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a1 = with_profile(owner, org)
    a2 = with_profile(owner, org, account_number="20208000999999999999", name="Второй")
    load(owner, org, a1["id"], "05.10.2026;10,00;A;;;")
    r = owner.get(
        base(org) + "/reports/summary", params={"date_from": "2026-11-01", "date_to": "2026-10-01"}
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_range"
    rows = owner.get(base(org) + "/reports/summary", params=Q).json()["rows"]
    assert len(rows) == 1
    assert (
        owner.get(base(org) + "/reports/summary", params={**Q, "account_id": a2["id"]}).json()[
            "rows"
        ]
        == []
    )


def test_matches_report_and_csv(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    cp = counterparty(owner, org, "=ООО Формула", "123456789")
    d = make_doc(owner, org, "100", number="СФ-9", cp=cp)
    t = load(owner, org, a["id"], "05.10.2026;100,00;СФ-9;;;")[0]
    assert confirm(owner, org, t["id"], (d, "100")).status_code == 201
    r = owner.get(base(org) + "/reports/matches", params=Q).json()
    assert r["total"] == 1 and not r["truncated"]
    row = r["rows"][0]
    assert row["document_number"] == "СФ-9" and row["amount"] == "100.00"
    assert row["transaction_id"] == t["id"] and row["document_id"] == d
    # отменённое сопоставление не попадает в отчёт
    mid = owner.get(base(org) + f"/transactions/{t['id']}").json()["matches"][0]["id"]
    csv_r = owner.get(base(org) + "/reports/matches.csv", params=Q)
    assert csv_r.status_code == 200
    assert csv_r.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_r.headers["content-disposition"]
    assert csv_r.headers["cache-control"] == "private, no-store"
    assert csv_r.text.startswith("﻿")
    assert "'=ООО Формула" in csv_r.text  # защита от формул
    owner.post(
        base(org) + f"/matches/{mid}/cancel",
        headers=h(owner),
    )
    assert owner.get(base(org) + "/reports/matches", params=Q).json()["total"] == 0
    s_csv = owner.get(base(org) + "/reports/summary.csv", params=Q)
    assert s_csv.status_code == 200 and "Поступления" in s_csv.text
    t_csv = owner.get(base(org) + "/reports/transactions.csv", params={**Q, "status": "UNMATCHED"})
    assert t_csv.status_code == 200 and "СФ-9" in t_csv.text


def test_report_permissions_and_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    load(owner, org, a["id"], "05.10.2026;100,00;X;;;")
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    paths = (
        "summary",
        "summary.csv",
        "matches",
        "matches.csv",
        "transactions.csv",
    )
    for p in paths:
        assert auditor.get(base(org) + f"/reports/{p}", params=Q).status_code == 200, p
        assert keeper.get(base(org) + f"/reports/{p}", params=Q).status_code == 403, p
    other, org_b = setup_org(new_client, "b@example.uz")
    assert other.get(base(org) + "/reports/summary", params=Q).status_code in (403, 404)
    assert other.get(base(org_b) + "/reports/summary", params=Q).json()["rows"] == []
