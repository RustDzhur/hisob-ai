"""Срез 5.2: правила проводок (вводит и утверждает бухгалтер) и проводки по утверждённым документам."""

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.files import pdf
from tests.helpers import csrf_headers, join, setup_org, upload_doc
from tests.integration.test_accounting import NewClient, acc, actions, base, h, trial

_n = 0


def api(org: str, path: str) -> str:
    return f"/api/v1/organizations/{org}{path}"


def accounts(c: TestClient, org: str) -> tuple[str, str, str]:
    return (
        acc(c, org, "1000", "Покупатели")["id"],
        acc(c, org, "2000", "Выручка")["id"],
        acc(c, org, "3000", "Налог")["id"],
    )


def rule_body(a: str, b: str, t: str, key: str = "sales", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rule_key": key,
        "name": "Реализация",
        "doc_type": "invoice",
        "lines": [
            {"account_id": a, "side": "debit", "amount_source": "total_amount"},
            {"account_id": b, "side": "credit", "amount_source": "total_minus_tax"},
            {"account_id": t, "side": "credit", "amount_source": "tax_total"},
        ],
    }
    body.update(over)
    return body


def mk_rule(c: TestClient, org: str, body: dict[str, Any]) -> Any:
    return c.post(base(org) + "/rules", json=body, headers=h(c))


def approve(c: TestClient, org: str, rid: str, basis: str = "Решение бухгалтера № 1") -> Any:
    return c.post(base(org) + f"/rules/{rid}/approve", json={"legal_basis": basis}, headers=h(c))


def approved_rule(c: TestClient, org: str, a: str, b: str, t: str, **over: Any) -> dict[str, Any]:
    r = mk_rule(c, org, rule_body(a, b, t, **over))
    assert r.status_code == 201, r.text
    ok = approve(c, org, r.json()["id"])
    assert ok.status_code == 200, ok.text
    out: dict[str, Any] = ok.json()
    return out


def doc(
    c: TestClient,
    org: str,
    total: str,
    tax: str | None,
    *,
    approve_it: bool = True,
    date_: str = "2026-10-05",
) -> str:
    """Утверждённый документ: строки без налога, налог — итогом по документу; итог = строки + налог."""
    global _n
    _n += 1
    did: str = upload_doc(c, org, pdf(f"rule-{org}-{_n}")).json()["id"]
    cp = c.post(
        api(org, "/counterparties"),
        json={"name": f"Контрагент {_n}", "tin": f"1234567{_n:02d}"},
        headers=csrf_headers(c),
    ).json()["id"]
    net = int(total) - int(tax or 0)
    body = {
        "expected_version": 1,
        "doc_type": "invoice",
        "number": f"Р-{_n}",
        "doc_date": date_,
        "counterparty_id": cp,
        "currency": "UZS",
        "total_amount": total,
        "tax_total": tax,
        "lines": [
            {"description": "Услуга", "quantity": "1", "unit_price": str(net), "amount": str(net)}
        ],
    }
    r = c.patch(api(org, f"/documents/{did}"), json=body, headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    if approve_it:
        a = c.post(
            api(org, f"/documents/{did}/approve"),
            json={"expected_version": r.json()["version"], "acknowledged": []},
            headers=csrf_headers(c),
        )
        assert a.status_code == 200, a.text
    return did


def proposals(c: TestClient, org: str, did: str) -> Any:
    return c.get(base(org) + f"/documents/{did}/proposals")


def post_doc(c: TestClient, org: str, did: str, rid: str, **extra: Any) -> Any:
    return c.post(
        base(org) + "/entries/from-document",
        json={"document_id": did, "rule_id": rid, **extra},
        headers=h(c),
    )


# ---------------- правила ----------------


def test_no_builtin_rules_and_validation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    assert owner.get(base(org) + "/rules").json()["total"] == 0  # встроенных правил нет
    a, b, t = accounts(owner, org)

    def code(body: dict[str, Any]) -> tuple[int, str]:
        r = mk_rule(owner, org, body)
        return r.status_code, r.json().get("code", "")

    bad = rule_body(a, b, t)
    bad["lines"][1]["amount_source"] = "total_amount"  # Дт итого, Кт итого + Кт налог: не сходится
    assert code(bad) == (422, "rule_not_balanced")
    only_debit = rule_body(
        a,
        b,
        t,
        lines=[
            {"account_id": a, "side": "debit", "amount_source": "total_amount"},
            {"account_id": b, "side": "debit", "amount_source": "total_amount"},
        ],
    )
    assert code(only_debit) == (422, "rule_not_balanced")
    assert code(rule_body(a, b, t, doc_type="nonsense")) == (422, "doc_type_unknown")
    assert code(rule_body(a, b, t, effective_from="2026-12-01", effective_to="2026-01-01")) == (
        422,
        "invalid_range",
    )
    group = acc(owner, org, "9", "Группа", allow_posting=False)["id"]
    arch = acc(owner, org, "8", "Архив")["id"]
    owner.post(base(org) + f"/accounts/{arch}/archive", headers=h(owner))
    assert code(rule_body(group, b, t)) == (422, "account_not_postable")
    assert code(rule_body(arch, b, t)) == (422, "account_archived")
    other, org2 = setup_org(new_client, "two@example.uz")
    foreign = acc(other, org2, "1")["id"]
    assert code(rule_body(foreign, b, t)) == (422, "account_not_found")
    assert (
        code(
            rule_body(
                a, b, t, lines=[{"account_id": a, "side": "debit", "amount_source": "total_amount"}]
            )
        )[0]
        == 422
    )  # одна строка
    assert (
        code(
            rule_body(
                a,
                b,
                t,
                lines=[
                    {"account_id": a, "side": "debit", "amount_source": "vat_rate"},
                    {"account_id": b, "side": "credit", "amount_source": "total_amount"},
                ],
            )
        )[0]
        == 422
    )
    assert mk_rule(owner, org, rule_body(a, b, t)).status_code == 201
    assert code(rule_body(a, b, t)) == (409, "rule_key_taken")
    # без налога: Дт итого / Кт итого — тоже сбалансированное правило
    simple = rule_body(
        a,
        b,
        t,
        key="simple",
        lines=[
            {"account_id": a, "side": "debit", "amount_source": "total_amount"},
            {"account_id": b, "side": "credit", "amount_source": "total_amount"},
        ],
    )
    assert mk_rule(owner, org, simple).status_code == 201
    # «налог» отдельной строкой: Дт итого, Кт (итого − налог) и Кт налог — сбалансировано (проверено выше); Дт налог / Кт налог тоже
    tax_only = rule_body(
        a,
        b,
        t,
        key="tax",
        lines=[
            {"account_id": a, "side": "debit", "amount_source": "tax_total"},
            {"account_id": t, "side": "credit", "amount_source": "tax_total"},
        ],
    )
    assert mk_rule(owner, org, tax_only).status_code == 201


def test_rule_lifecycle_versions_and_immutability(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    r1 = mk_rule(owner, org, rule_body(a, b, t)).json()
    assert (r1["status"], r1["version"], r1["legal_basis"]) == ("DRAFT", 1, None)
    upd = owner.patch(
        base(org) + f"/rules/{r1['id']}",
        json={"name": "Реализация (чернов.)", "description": "d"},
        headers=h(owner),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "Реализация (чернов.)"
    # утверждение требует основания
    for basis in ("", "   "):
        assert (
            owner.post(
                base(org) + f"/rules/{r1['id']}/approve",
                json={"legal_basis": basis},
                headers=h(owner),
            ).status_code
            == 422
        )
    assert (
        owner.post(base(org) + f"/rules/{r1['id']}/approve", json={}, headers=h(owner)).status_code
        == 422
    )
    ok = approve(owner, org, r1["id"], "Письмо бухгалтера от 01.10")
    assert (
        ok.status_code == 200
        and ok.json()["status"] == "APPROVED"
        and ok.json()["approved_by_name"]
        and ok.json()["legal_basis"] == "Письмо бухгалтера от 01.10"
    )
    # утверждённое: ни править, ни удалять, ни утверждать повторно
    assert (
        owner.patch(base(org) + f"/rules/{r1['id']}", json={"name": "x"}, headers=h(owner)).json()[
            "code"
        ]
        == "rule_not_draft"
    )
    assert (
        owner.delete(base(org) + f"/rules/{r1['id']}", headers=h(owner)).json()["code"]
        == "rule_not_draft"
    )
    assert approve(owner, org, r1["id"]).json()["code"] == "rule_not_draft"
    # на уровне БД строки утверждённого правила заморожены
    with pytest.raises(DBAPIError, match="frozen"), admin_engine.begin() as c:
        c.execute(text("UPDATE posting_rule_line SET amount_source = 'tax_total'"))
    with pytest.raises(DBAPIError, match="frozen"), admin_engine.begin() as c:
        c.execute(text("DELETE FROM posting_rule_line"))
    # статус «утверждено» без основания БД не принимает
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(text("UPDATE posting_rule SET legal_basis = NULL"))
    # новая версия
    v2 = owner.post(base(org) + f"/rules/{r1['id']}/new-version", headers=h(owner))
    assert v2.status_code == 201 and (v2.json()["version"], v2.json()["status"]) == (2, "DRAFT")
    assert [(x["account_code"], x["side"], x["amount_source"]) for x in v2.json()["lines"]] == [
        (x["account_code"], x["side"], x["amount_source"]) for x in ok.json()["lines"]
    ]
    assert (
        owner.post(base(org) + f"/rules/{r1['id']}/new-version", headers=h(owner)).json()["code"]
        == "rule_draft_exists"
    )
    owner.patch(
        base(org) + f"/rules/{v2.json()['id']}",
        json={"effective_from": "2026-11-01"},
        headers=h(owner),
    )
    assert approve(owner, org, v2.json()["id"], "Новое основание").status_code == 200
    statuses = {x["version"]: x["status"] for x in owner.get(base(org) + "/rules").json()["items"]}
    assert statuses == {1: "RETIRED", 2: "APPROVED"}  # прежняя версия выведена из действия
    assert (
        owner.post(base(org) + f"/rules/{v2.json()['id']}/retire", headers=h(owner)).json()[
            "status"
        ]
        == "RETIRED"
    )
    assert (
        owner.post(base(org) + f"/rules/{v2.json()['id']}/retire", headers=h(owner)).json()["code"]
        == "rule_not_approved"
    )
    d = mk_rule(owner, org, rule_body(a, b, t, key="tmp")).json()
    assert (
        owner.delete(base(org) + f"/rules/{d['id']}", headers=h(owner)).status_code == 204
    )  # черновик можно удалить
    assert owner.get(base(org) + f"/rules/{d['id']}").status_code == 404
    acts = actions(admin_engine)
    assert {
        "posting_rule.created",
        "posting_rule.updated",
        "posting_rule.approved",
        "posting_rule.retired",
        "posting_rule.deleted",
    } <= set(acts)


def test_rule_permissions(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    r = mk_rule(owner, org, rule_body(a, b, t)).json()
    accountant = join(new_client, owner, org, "acc@example.uz", "accountant")
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert accountant.get(base(org) + f"/rules/{r['id']}").status_code == 200
    assert approve(accountant, org, r["id"]).status_code == 200
    assert auditor.get(base(org) + "/rules").status_code == 200
    assert mk_rule(auditor, org, rule_body(a, b, t, key="x")).status_code == 403
    assert approve(auditor, org, r["id"]).status_code == 403
    assert keeper.get(base(org) + "/rules").status_code == 403
    other, org2 = setup_org(new_client, "two@example.uz")
    assert other.get(base(org2) + f"/rules/{r['id']}").status_code == 404
    assert other.get(base(org) + "/rules").status_code == 404


# ---------------- предложения и проведение по документу ----------------


def test_proposal_amounts_come_from_the_document_and_the_rule(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    draft = doc(owner, org, "1120", "120", approve_it=False)
    assert proposals(owner, org, draft).json()["code"] == "document_not_approved"
    d = doc(owner, org, "1120", "120")
    assert proposals(owner, org, d).json() == []  # правил нет — предложений нет
    approved_rule(owner, org, a, b, t)
    mk_rule(owner, org, rule_body(a, b, t, key="draft-only"))  # черновик не предлагается
    mk_rule(owner, org, rule_body(a, b, t, key="other-type", doc_type="act"))
    props = proposals(owner, org, d).json()
    assert len(props) == 1
    p = props[0]
    assert (
        p["problems"] == []
        and p["currency"] == "UZS"
        and p["entry_date"] == "2026-10-05"
        and p["legal_basis"] == "Решение бухгалтера № 1"
    )
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in p["lines"]] == [
        ("1000", "1120.00", "0.00"),
        ("2000", "0.00", "1000.00"),
        ("3000", "0.00", "120.00"),
    ]
    # без налога нулевая строка не проводится
    d0 = doc(owner, org, "500", "0")
    p0 = proposals(owner, org, d0).json()[0]
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in p0["lines"]] == [
        ("1000", "500.00", "0.00"),
        ("2000", "0.00", "500.00"),
    ]
    # налог не указан вовсе — правило с налогом не может посчитать
    dn = doc(owner, org, "500", None)
    assert proposals(owner, org, dn).json()[0]["problems"] == ["document_tax_missing"]


def test_posting_from_document_is_idempotent_traceable_and_reversible(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    rule = approved_rule(owner, org, a, b, t)
    d = doc(owner, org, "1120", "120")
    r = post_doc(owner, org, d, rule["id"])
    assert r.status_code == 201, r.text
    e = r.json()
    assert (
        e["source_document"]["id"] == d
        and (e["rule"]["key"], e["rule"]["version"]) == ("sales", 1)
        and e["total"] == "1120.00"
    )
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in e["lines"]] == [
        ("1000", "1120.00", "0.00"),
        ("2000", "0.00", "1000.00"),
        ("3000", "0.00", "120.00"),
    ]
    assert "journal_entry.created_from_rule" in actions(admin_engine)
    # повтор и новая версия того же правила не создают вторую проводку
    again = post_doc(owner, org, d, rule["id"])
    assert again.status_code == 409 and again.json()["code"] == "document_already_posted"
    assert proposals(owner, org, d).json()[0]["posted_entry_id"] == e["id"]
    v2 = owner.post(base(org) + f"/rules/{rule['id']}/new-version", headers=h(owner)).json()
    approve(owner, org, v2["id"], "Основание v2")
    assert post_doc(owner, org, d, v2["id"]).json()["code"] == "document_already_posted"
    assert (
        post_doc(owner, org, d, rule["id"]).status_code == 409
    )  # прежняя версия выведена из действия или уже проведена
    # ведомость отражает проводку
    t_ = trial(owner, org, "2026-10-01", "2026-10-31")
    assert t_["balanced"] and t_["totals"]["turnover_debit"] == "1120.00"
    # после сторно документ можно провести заново
    rv = owner.post(base(org) + f"/entries/{e['id']}/reverse", json={}, headers=h(owner))
    assert rv.status_code == 201
    assert proposals(owner, org, d).json()[0]["posted_entry_id"] is None
    assert post_doc(owner, org, d, v2["id"]).status_code == 201


def test_posting_guards(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    rule = approved_rule(
        owner, org, a, b, t, effective_from="2026-10-06", effective_to="2026-10-31"
    )
    inside = doc(owner, org, "1120", "120", date_="2026-10-08")
    outside = doc(owner, org, "1120", "120", date_="2026-10-02")
    assert proposals(owner, org, outside).json() == []  # правило не действует на дату документа
    forced = post_doc(owner, org, outside, rule["id"])
    assert forced.status_code == 422 and forced.json()["code"] == "rule_not_effective"
    notready = doc(owner, org, "1120", None, date_="2026-10-08")
    nr = post_doc(owner, org, notready, rule["id"])
    assert (
        nr.status_code == 422
        and nr.json()["code"] == "proposal_not_ready"
        and nr.json()["details"][0]["code"] == "document_tax_missing"
    )
    draft = doc(owner, org, "1120", "120", approve_it=False, date_="2026-10-09")
    assert post_doc(owner, org, draft, rule["id"]).json()["code"] == "document_not_approved"
    wrong = mk_rule(owner, org, rule_body(a, b, t, key="act-rule", doc_type="act")).json()
    approve(owner, org, wrong["id"])
    assert post_doc(owner, org, inside, wrong["id"]).json()["code"] == "rule_doc_type_mismatch"
    unapproved = mk_rule(owner, org, rule_body(a, b, t, key="draft-rule")).json()
    assert post_doc(owner, org, inside, unapproved["id"]).json()["code"] == "rule_not_approved"
    assert (
        owner.post(
            base(org) + "/entries/from-document",
            json={"document_id": inside, "rule_id": rule["id"], "entry_date": "2026-12-01"},
            headers=h(owner),
        ).json()["code"]
        == "rule_not_effective"
    )
    # закрытый период
    period = owner.post(
        base(org) + "/periods", json={"year": 2026, "month": 10}, headers=h(owner)
    ).json()
    owner.post(base(org) + f"/periods/{period['id']}/close", headers=h(owner))
    closed = post_doc(owner, org, inside, rule["id"])
    assert closed.status_code == 409 and closed.json()["code"] == "period_closed"
    owner.post(base(org) + f"/periods/{period['id']}/reopen", headers=h(owner))
    assert post_doc(owner, org, inside, rule["id"]).status_code == 201
    # права: проводить может только accounting.post; аудитор видит предложения, но не проводит
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    other = doc(owner, org, "1120", "120", date_="2026-10-09")
    assert proposals(auditor, org, other).status_code == 200
    assert post_doc(auditor, org, other, rule["id"]).status_code == 403
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert proposals(keeper, org, other).status_code == 403


def test_currency_must_be_enabled_and_foreign_documents_are_hidden(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    rule = approved_rule(owner, org, a, b, t)
    d = doc(owner, org, "1120", "120")
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE organization_currency SET enabled = false WHERE currency_code = 'UZS' AND is_base = false"
            )
        )
    other, org2 = setup_org(new_client, "two@example.uz")
    assert proposals(other, org2, d).status_code == 404
    assert post_doc(other, org2, d, rule["id"]).status_code in (404, 422)
    assert proposals(other, org, d).status_code == 404


def test_concurrent_posting_from_one_document(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    a, b, t = accounts(owner, org)
    rule = approved_rule(owner, org, a, b, t)
    d = doc(owner, org, "1120", "120")
    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            out.append(
                c.post(
                    base(org) + "/entries/from-document",
                    json={"document_id": d, "rule_id": rule["id"]},
                    headers=hh,
                ).status_code
            )

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert sorted(out) == [201, 409, 409, 409], out
    assert owner.get(base(org) + "/entries").json()["total"] == 1


def test_rls_on_rule_tables(new_client: NewClient, app_engine: Engine) -> None:
    a_owner, org_a = setup_org(new_client, "a@example.uz")
    b_owner, org_b = setup_org(new_client, "b@example.uz")
    for c, org in ((a_owner, org_a), (b_owner, org_b)):
        x, y, z = accounts(c, org)
        approved_rule(c, org, x, y, z)
    tables = ("posting_rule", "posting_rule_line")
    with app_engine.connect() as conn:
        for t in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for t in tables:
            assert {r[0] for r in conn.execute(text(f"SELECT organization_id::text FROM {t}"))} == {
                org_a
            }, t
