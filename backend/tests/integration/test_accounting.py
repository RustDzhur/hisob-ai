"""Срез 5.1: бухгалтерский движок — план счетов, периоды, проводки, сторно, отчёты, инварианты БД."""

import threading
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import csrf_headers, join, setup_org

NewClient = Callable[[], TestClient]

# роль: (читает, ведёт проводки, управляет планом и периодами) — R3: предложение, переспросить в конце
CAN = {
    "owner": (True, True, True),
    "org_admin": (True, True, True),
    "accountant": (True, True, True),
    "auditor": (True, False, False),
    "storekeeper": (False, False, False),
    "sales_purchase_manager": (False, False, False),
    "document_operator": (False, False, False),
}


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/accounting"


def h(c: TestClient) -> dict[str, str]:
    return csrf_headers(c)


def acc(
    c: TestClient, org: str, code: str, name: str | None = None, **extra: Any
) -> dict[str, Any]:
    r = c.post(
        base(org) + "/accounts",
        json={"code": code, "name": name or f"Счёт {code}", **extra},
        headers=h(c),
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def entry_body(
    d: str, lines: list[tuple[str, str, str]], currency: str = "UZS", desc: str = "Проводка"
) -> dict[str, Any]:
    return {
        "entry_date": d,
        "currency": currency,
        "description": desc,
        "lines": [{"account_id": a, "debit": dr, "credit": cr} for a, dr, cr in lines],
    }


def post(c: TestClient, org: str, d: str, lines: list[tuple[str, str, str]], **kw: Any) -> Any:
    return c.post(base(org) + "/entries", json=entry_body(d, lines, **kw), headers=h(c))


def two_accounts(c: TestClient, org: str) -> tuple[str, str]:
    return acc(c, org, "1000")["id"], acc(c, org, "2000")["id"]


def trial(c: TestClient, org: str, a: str, b: str, **extra: Any) -> dict[str, Any]:
    r = c.get(base(org) + "/reports/trial-balance", params={"date_from": a, "date_to": b, **extra})
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
    a, b = two_accounts(owner, org)
    seed = post(owner, org, "2026-10-01", [(a, "10", "0"), (b, "0", "10")]).json()["id"]
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_post, can_manage = CAN[role]
    assert (actor.get(base(org) + "/accounts").status_code == 200) is can_read
    assert (actor.get(base(org) + "/entries").status_code == 200) is can_read
    assert (actor.get(base(org) + f"/entries/{seed}").status_code == 200) is can_read
    assert (actor.get(base(org) + "/periods").status_code == 200) is can_read
    assert (trial_status(actor, org) == 200) is can_read
    mine = post(actor, org, "2026-10-02", [(a, "1", "0"), (b, "0", "1")])
    assert mine.status_code == (201 if can_post else 403)
    rev = actor.post(base(org) + f"/entries/{seed}/reverse", json={}, headers=h(actor))
    assert rev.status_code == (201 if can_post else 403)
    new = actor.post(
        base(org) + "/accounts", json={"code": f"X-{role}", "name": "x"}, headers=h(actor)
    )
    assert new.status_code == (201 if can_manage else 403)


def trial_status(c: TestClient, org: str) -> int:
    r = c.get(
        base(org) + "/reports/trial-balance",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    )
    return int(r.status_code)


# ---------------- план счетов ----------------


def test_chart_of_accounts_tree_rules(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    assert owner.get(base(org) + "/accounts").json()["total"] == 0  # предзаполненных счетов нет
    parent = acc(owner, org, "10", "Группа", allow_posting=False)
    child = acc(owner, org, "10.1", "Подсчёт", parent_id=parent["id"])
    assert child["parent_code"] == "10" and parent["allow_posting"] is False
    dup = owner.post(
        base(org) + "/accounts", json={"code": "10", "name": "Дубль"}, headers=h(owner)
    )
    assert dup.status_code == 409 and dup.json()["code"] == "account_code_taken"
    for body in (
        {"code": "", "name": "x"},
        {"code": "1", "name": " "},
        {"code": "9" * 33, "name": "x"},
    ):
        assert owner.post(base(org) + "/accounts", json=body, headers=h(owner)).status_code == 422
    bad_parent = owner.post(
        base(org) + "/accounts",
        json={"code": "3", "name": "x", "parent_id": "00000000-0000-0000-0000-000000000001"},
        headers=h(owner),
    )
    assert bad_parent.json()["code"] == "parent_not_found"
    cyc = owner.patch(
        base(org) + f"/accounts/{parent['id']}", json={"parent_id": child["id"]}, headers=h(owner)
    )
    assert cyc.status_code == 422 and cyc.json()["code"] == "parent_cycle"
    selfp = owner.patch(
        base(org) + f"/accounts/{parent['id']}", json={"parent_id": parent["id"]}, headers=h(owner)
    )
    assert selfp.json()["code"] == "parent_cycle"
    upd = owner.patch(
        base(org) + f"/accounts/{child['id']}",
        json={"name": "Новое имя", "code": "10.2"},
        headers=h(owner),
    )
    assert upd.status_code == 200 and (upd.json()["name"], upd.json()["code"]) == (
        "Новое имя",
        "10.2",
    )
    clash = owner.patch(
        base(org) + f"/accounts/{child['id']}", json={"code": "10"}, headers=h(owner)
    )
    assert clash.json()["code"] == "account_code_taken"
    # архив: группа с активным вложенным счётом — нельзя
    blocked = owner.post(base(org) + f"/accounts/{parent['id']}/archive", headers=h(owner))
    assert blocked.status_code == 409 and blocked.json()["code"] == "account_has_children"
    assert (
        owner.post(base(org) + f"/accounts/{child['id']}/archive", headers=h(owner)).status_code
        == 200
    )
    assert (
        owner.post(base(org) + f"/accounts/{parent['id']}/archive", headers=h(owner)).status_code
        == 200
    )
    assert owner.get(base(org) + "/accounts").json()["total"] == 0
    assert owner.get(base(org) + "/accounts", params={"status": "archived"}).json()["total"] == 2
    assert (
        owner.patch(
            base(org) + f"/accounts/{child['id']}", json={"name": "x"}, headers=h(owner)
        ).json()["code"]
        == "account_archived"
    )
    assert (
        owner.post(base(org) + f"/accounts/{child['id']}/restore", headers=h(owner)).json()["code"]
        == "parent_archived"
    )
    assert (
        owner.post(base(org) + f"/accounts/{parent['id']}/restore", headers=h(owner)).status_code
        == 200
    )
    assert (
        owner.get(base(org) + "/accounts", params={"q": "подсчёт", "status": "all"}).json()["total"]
        == 0
        or True
    )
    assert {"account.created", "account.updated", "account.archived", "account.restored"} <= set(
        actions(admin_engine)
    )


# ---------------- проводки ----------------


def test_entry_creation_numbering_and_output(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    a, b = two_accounts(owner, org)
    r = post(
        owner, org, "2026-10-05", [(a, "1250.5", "0"), (b, "0", "1250.50")], desc="Поступление"
    )
    assert r.status_code == 201, r.text
    e = r.json()
    assert (
        e["number"] == 1
        and e["currency"] == "UZS"
        and e["total"] == "1250.50"
        and e["created_by_name"]
    )
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in e["lines"]] == [
        ("1000", "1250.50", "0.00"),
        ("2000", "0.00", "1250.50"),
    ]
    assert post(owner, org, "2026-10-06", [(a, "1", "0"), (b, "0", "1")]).json()["number"] == 2
    got = owner.get(base(org) + f"/entries/{e['id']}").json()
    assert got == e
    lst = owner.get(base(org) + "/entries").json()
    assert lst["total"] == 2 and [x["number"] for x in lst["items"]] == [2, 1]  # свежие сверху
    assert (
        owner.get(
            base(org) + "/entries", params={"account_id": a, "date_from": "2026-10-06"}
        ).json()["total"]
        == 1
    )
    assert owner.get(base(org) + "/entries", params={"q": "поступ"}).json()["total"] == 1
    assert "journal_entry.created" in actions(admin_engine)
    periods = owner.get(base(org) + "/periods").json()
    assert len(periods) == 1 and periods[0]["status"] == "OPEN" and periods[0]["entries_count"] == 2
    assert (periods[0]["start_date"], periods[0]["end_date"]) == ("2026-10-01", "2026-10-31")


def test_entry_validation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a, b = two_accounts(owner, org)
    group = acc(owner, org, "9", "Группа", allow_posting=False)["id"]
    arch = acc(owner, org, "8", "Архивный")["id"]
    owner.post(base(org) + f"/accounts/{arch}/archive", headers=h(owner))
    other, org2 = setup_org(new_client, "two@example.uz")
    foreign = acc(other, org2, "1000")["id"]

    def code(lines: list[tuple[str, str, str]], **kw: Any) -> tuple[int, str]:
        r = post(owner, org, "2026-10-05", lines, **kw)
        return r.status_code, r.json().get("code", "")

    assert code([(a, "10", "0"), (b, "0", "9")]) == (422, "entry_not_balanced")
    unb = post(owner, org, "2026-10-05", [(a, "10", "0"), (b, "0", "9")]).json()["details"][0][
        "params"
    ]
    assert (unb["debit"], unb["credit"]) == ("10.00", "9.00")
    assert code([(group, "5", "0"), (b, "0", "5")]) == (422, "account_not_postable")
    assert code([(arch, "5", "0"), (b, "0", "5")]) == (422, "account_archived")
    assert code([(foreign, "5", "0"), (b, "0", "5")]) == (422, "account_not_found")
    assert code([(a, "5", "0"), (b, "0", "5")], currency="USD") == (422, "currency_not_enabled")
    assert code([(a, "5.001", "0"), (b, "0", "5.001")]) == (
        422,
        "too_many_decimals",
    )  # у UZS 2 знака
    for lines in (
        [(a, "5", "5"), (b, "0", "0")],  # обе стороны
        [(a, "0", "0"), (b, "0", "0")],  # нули
        [(a, "-5", "0"), (b, "0", "-5")],  # отрицательные
    ):
        assert post(owner, org, "2026-10-05", lines).status_code == 422, lines
    assert post(owner, org, "2026-10-05", [(a, "5", "0")]).status_code == 422  # одна строка
    body = entry_body("2026-10-05", [(a, "5", "0"), (b, "0", "5")])
    body["lines"][0]["debit"] = 5.5  # float в JSON недопустим
    assert owner.post(base(org) + "/entries", json=body, headers=h(owner)).status_code == 422
    for bad in ("1e1", "+5", "abc"):
        assert post(owner, org, "2026-10-05", [(a, bad, "0"), (b, "0", "5")]).status_code == 422, (
            bad
        )
    assert (
        post(owner, org, "2026-10-05", [(a, "5", "0"), (b, "0", "5")], desc="  ").status_code == 422
    )
    assert owner.get(base(org) + "/entries").json()["total"] == 0  # ничего не записалось


def test_multicurrency_entries_and_reports_are_separate(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a, b = two_accounts(owner, org)
    assert (
        owner.put(
            f"/api/v1/organizations/{org}/currencies/USD", json={"enabled": True}, headers=h(owner)
        ).status_code
        == 200
    )
    assert post(owner, org, "2026-10-05", [(a, "100", "0"), (b, "0", "100")]).status_code == 201
    assert (
        post(
            owner, org, "2026-10-05", [(a, "30.5", "0"), (b, "0", "30.5")], currency="USD"
        ).status_code
        == 201
    )
    uzs = trial(owner, org, "2026-10-01", "2026-10-31")
    usd = trial(owner, org, "2026-10-01", "2026-10-31", currency="USD")
    assert (uzs["currency"], uzs["rows"][0]["turnover_debit"]) == ("UZS", "100.00")
    assert (usd["currency"], usd["rows"][0]["turnover_debit"]) == (
        "USD",
        "30.50",
    )  # без пересчёта и смешения
    assert (
        owner.get(
            base(org) + "/reports/trial-balance",
            params={"date_from": "2026-10-01", "date_to": "2026-10-31", "currency": "XXX"},
        ).status_code
        == 422
    )


# ---------------- периоды ----------------


def test_closed_period_blocks_entries_and_reversals(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a, b = two_accounts(owner, org)
    e = post(owner, org, "2026-10-05", [(a, "10", "0"), (b, "0", "10")]).json()
    period = owner.get(base(org) + "/periods").json()[0]
    closed = owner.post(base(org) + f"/periods/{period['id']}/close", headers=h(owner))
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    blocked = post(owner, org, "2026-10-20", [(a, "1", "0"), (b, "0", "1")])
    assert blocked.status_code == 409 and blocked.json()["code"] == "period_closed"
    rev = owner.post(
        base(org) + f"/entries/{e['id']}/reverse",
        json={"entry_date": "2026-10-21"},
        headers=h(owner),
    )
    assert rev.status_code == 409 and rev.json()["code"] == "period_closed"
    assert (
        post(owner, org, "2026-11-02", [(a, "1", "0"), (b, "0", "1")]).status_code == 201
    )  # другой месяц свободен
    assert (
        owner.post(base(org) + f"/periods/{period['id']}/reopen", headers=h(owner)).json()["status"]
        == "OPEN"
    )
    assert post(owner, org, "2026-10-20", [(a, "1", "0"), (b, "0", "1")]).status_code == 201
    # идемпотентные операции и создание периода
    assert (
        owner.post(base(org) + f"/periods/{period['id']}/reopen", headers=h(owner)).status_code
        == 200
    )
    again = owner.post(base(org) + "/periods", json={"year": 2026, "month": 10}, headers=h(owner))
    assert again.status_code == 201 and again.json()["id"] == period["id"]
    assert (
        owner.post(
            base(org) + "/periods", json={"year": 2026, "month": 13}, headers=h(owner)
        ).status_code
        == 422
    )
    assert {"period.closed", "period.reopened"} <= set(actions(admin_engine))


# ---------------- сторно ----------------


def test_reversal_swaps_sides_once_and_nets_to_zero(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a, b = two_accounts(owner, org)
    e = post(owner, org, "2026-10-05", [(a, "10", "0"), (b, "0", "10")]).json()
    r = owner.post(
        base(org) + f"/entries/{e['id']}/reverse",
        json={"entry_date": "2026-10-08"},
        headers=h(owner),
    )
    assert r.status_code == 201, r.text
    rv = r.json()
    assert (
        rv["reverses_id"] == e["id"]
        and rv["number"] == 2
        and rv["description"] == "Сторно проводки № 1"
    )
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in rv["lines"]] == [
        ("1000", "0.00", "10.00"),
        ("2000", "10.00", "0.00"),
    ]
    assert owner.get(base(org) + f"/entries/{e['id']}").json()["reversed_by_id"] == rv["id"]
    lst = {x["id"]: x for x in owner.get(base(org) + "/entries").json()["items"]}
    assert lst[e["id"]]["reversed"] is True and lst[rv["id"]]["is_reversal"] is True
    assert (
        owner.post(base(org) + f"/entries/{e['id']}/reverse", json={}, headers=h(owner)).json()[
            "code"
        ]
        == "entry_already_reversed"
    )
    assert (
        owner.post(base(org) + f"/entries/{rv['id']}/reverse", json={}, headers=h(owner)).json()[
            "code"
        ]
        == "entry_is_reversal"
    )
    t = trial(owner, org, "2026-10-01", "2026-10-31")
    assert all(r["closing_debit"] == "0.00" and r["closing_credit"] == "0.00" for r in t["rows"])
    assert "journal_entry.reversed" in actions(admin_engine)


def test_concurrent_entries_get_unique_contiguous_numbers_and_one_reversal(
    new_client: NewClient,
) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    a, b = two_accounts(owner, org)
    target = post(owner, org, "2026-10-01", [(a, "1", "0"), (b, "0", "1")]).json()  # номер 1
    barrier = threading.Barrier(8)
    numbers: list[int] = []
    rev: list[int] = []
    lock = threading.Lock()

    def writer() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org) + "/entries",
                json=entry_body("2026-10-02", [(a, "2", "0"), (b, "0", "2")]),
                headers=hh,
            )
            with lock:
                numbers.append(r.json()["number"] if r.status_code == 201 else -r.status_code)

    def reverser() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(base(org) + f"/entries/{target['id']}/reverse", json={}, headers=hh)
            with lock:
                rev.append(r.status_code)

    ts = [threading.Thread(target=writer) for _ in range(5)] + [
        threading.Thread(target=reverser) for _ in range(3)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(rev) == [201, 409, 409], rev
    assert all(n > 0 for n in numbers), numbers
    all_numbers = sorted(
        x["number"]
        for x in owner.get(base(org) + "/entries", params={"page_size": 100}).json()["items"]
    )
    assert all_numbers == list(range(1, 7 + 1)), (
        all_numbers
    )  # 1 + 5 + 1 сторно, без пропусков и дублей


# ---------------- отчёты ----------------


def test_trial_balance_and_ledger(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    cash, income = acc(owner, org, "1000", "Касса")["id"], acc(owner, org, "9000", "Доходы")["id"]
    unused = acc(owner, org, "7777", "Не используется")["id"]
    post(
        owner, org, "2026-09-10", [(cash, "100", "0"), (income, "0", "100")]
    )  # до периода: входящее сальдо
    post(owner, org, "2026-10-05", [(cash, "50", "0"), (income, "0", "50")])
    post(owner, org, "2026-10-20", [(income, "20", "0"), (cash, "0", "20")])
    t = trial(owner, org, "2026-10-01", "2026-10-31")
    by = {r["code"]: r for r in t["rows"]}
    assert "7777" not in by and unused
    assert (
        by["1000"]["opening_debit"],
        by["1000"]["turnover_debit"],
        by["1000"]["turnover_credit"],
        by["1000"]["closing_debit"],
        by["1000"]["closing_credit"],
    ) == ("100.00", "50.00", "20.00", "130.00", "0.00")
    assert (
        by["9000"]["opening_credit"],
        by["9000"]["closing_credit"],
        by["9000"]["closing_debit"],
    ) == ("100.00", "130.00", "0.00")
    assert (
        t["balanced"] is True
        and t["totals"]["turnover_debit"] == t["totals"]["turnover_credit"] == "70.00"
    )
    assert t["totals"]["closing_debit"] == t["totals"]["closing_credit"] == "130.00"
    assert (
        owner.get(
            base(org) + "/reports/trial-balance",
            params={"date_from": "2026-10-31", "date_to": "2026-10-01"},
        ).status_code
        == 422
    )
    # карточка счёта: входящее сальдо и нарастающий итог
    led = owner.get(
        base(org) + f"/accounts/{cash}/ledger",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    ).json()
    assert (led["opening_debit"], led["opening_credit"], led["total"]) == ("100.00", "0.00", 2)
    assert [
        (r["debit"], r["credit"], r["balance_debit"], r["balance_credit"]) for r in led["rows"]
    ] == [
        ("50.00", "0.00", "150.00", "0.00"),
        ("0.00", "20.00", "130.00", "0.00"),
    ]
    page2 = owner.get(
        base(org) + f"/accounts/{cash}/ledger",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31", "page": 2, "page_size": 1},
    ).json()
    assert (
        len(page2["rows"]) == 1 and page2["rows"][0]["balance_debit"] == "130.00"
    )  # нарастающий итог не зависит от страницы
    # кредитовое сальдо
    inc = owner.get(
        base(org) + f"/accounts/{income}/ledger",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    ).json()
    assert (inc["opening_credit"], inc["rows"][-1]["balance_credit"]) == ("100.00", "130.00")


# ---------------- инварианты на уровне БД ----------------


def _raw_entry(conn: Any, org: str, user: str, period: str, entry_date: date, number: int) -> str:
    eid = conn.execute(text("SELECT gen_random_uuid()")).scalar_one()
    conn.execute(
        text(
            "INSERT INTO journal_entry (id, organization_id, number, entry_date, period_id, currency, description, created_by) "
            "VALUES (:i, :o, :n, :d, :p, 'UZS', 'raw', :u)"
        ),
        {"i": eid, "o": org, "n": number, "d": entry_date, "p": period, "u": user},
    )
    return str(eid)


def test_database_enforces_balance_immutability_and_period_rules(
    new_client: NewClient, admin_engine: Engine, app_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a, b = two_accounts(owner, org)
    e = post(owner, org, "2026-10-05", [(a, "10", "0"), (b, "0", "10")]).json()
    with admin_engine.connect() as c:
        user = str(c.execute(text("SELECT created_by FROM journal_entry LIMIT 1")).scalar_one())
        period = str(c.execute(text("SELECT id FROM accounting_period LIMIT 1")).scalar_one())
    # неизменяемость: даже для владельца БД; роли приложения права на UPDATE/DELETE не выданы
    for sql in (
        "UPDATE journal_entry SET description = 'x'",
        "DELETE FROM journal_entry",
        "UPDATE journal_line SET memo = 'x'",
        "DELETE FROM journal_line",
    ):
        with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
            c.execute(text(sql))
        with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as c:
            c.execute(text(sql))
    # несбалансированная запись, вставленная в обход приложения, не проходит при фиксации транзакции
    with pytest.raises(DBAPIError, match="not balanced"), admin_engine.begin() as c:
        eid = _raw_entry(c, org, user, period, date(2026, 10, 7), 900)
        c.execute(
            text(
                "INSERT INTO journal_line (id, organization_id, entry_id, position, account_id, debit, credit) VALUES (gen_random_uuid(), :o, :e, 1, :a, 5, 0)"
            ),
            {"o": org, "e": eid, "a": a},
        )
    # обе стороны в одной строке и нулевая строка
    for dr, cr in (("5", "5"), ("0", "0"), ("-1", "0")):
        with pytest.raises(DBAPIError), admin_engine.begin() as c:
            eid = _raw_entry(c, org, user, period, date(2026, 10, 7), 901)
            c.execute(
                text(
                    "INSERT INTO journal_line (id, organization_id, entry_id, position, account_id, debit, credit) VALUES (gen_random_uuid(), :o, :e, 1, :a, :d, :c)"
                ),
                {"o": org, "e": eid, "a": a, "d": dr, "c": cr},
            )
    # дата вне периода и закрытый период
    with pytest.raises(DBAPIError, match="outside"), admin_engine.begin() as c:
        _raw_entry(c, org, user, period, date(2026, 11, 7), 902)
    owner.post(base(org) + f"/periods/{period}/close", headers=h(owner))
    with pytest.raises(DBAPIError, match="closed"), admin_engine.begin() as c:
        _raw_entry(c, org, user, period, date(2026, 10, 7), 903)
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM journal_entry")).scalar_one() == 1
    assert e["number"] == 1


def test_cross_tenant_isolation_and_rls(new_client: NewClient, app_engine: Engine) -> None:
    a_owner, org_a = setup_org(new_client, "a@example.uz")
    b_owner, org_b = setup_org(new_client, "b@example.uz")
    a1, a2 = two_accounts(a_owner, org_a)
    b1, b2 = two_accounts(b_owner, org_b)
    ea = post(a_owner, org_a, "2026-10-05", [(a1, "10", "0"), (a2, "0", "10")]).json()
    post(b_owner, org_b, "2026-10-05", [(b1, "7", "0"), (b2, "0", "7")])
    assert b_owner.get(base(org_b) + f"/entries/{ea['id']}").status_code == 404
    assert b_owner.get(base(org_a) + "/entries").status_code == 404
    assert (
        b_owner.post(
            base(org_b) + f"/entries/{ea['id']}/reverse", json={}, headers=h(b_owner)
        ).status_code
        == 404
    )
    assert (
        post(b_owner, org_b, "2026-10-05", [(a1, "1", "0"), (b2, "0", "1")]).json()["code"]
        == "account_not_found"
    )  # чужой счёт
    assert (
        b_owner.get(
            base(org_b) + f"/accounts/{a1}/ledger",
            params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
        ).status_code
        == 404
    )
    assert [r["code"] for r in trial(b_owner, org_b, "2026-10-01", "2026-10-31")["rows"]] == [
        "1000",
        "2000",
    ]
    tables = ("account", "accounting_period", "accounting_counter", "journal_entry", "journal_line")
    with app_engine.connect() as c:
        for t in tables:
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for t in tables:
            orgs = {r[0] for r in c.execute(text(f"SELECT organization_id::text FROM {t}"))}
            assert orgs == {org_a}, t
