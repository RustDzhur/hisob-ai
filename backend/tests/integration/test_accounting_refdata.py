"""Срез 5.3: курсы валют (вручную и с cbu.uz), налоговые коды, отчёт в базовой валюте, экспорт CSV."""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.modules.accounting import cbu
from tests.helpers import create_org, csrf_headers, join, register, setup_org
from tests.integration.test_accounting import NewClient, acc, actions, base, h
from tests.integration.test_accounting import post as post_entry
from tests.integration.test_accounting_rules import accounts, approved_rule, doc, proposals


def enable(c: TestClient, org: str, code: str) -> None:
    r = c.put(
        f"/api/v1/organizations/{org}/currencies/{code}", json={"enabled": True}, headers=h(c)
    )
    assert r.status_code == 200, r.text


def put_rate(c: TestClient, org: str, currency: str, d: str, rate: str, nominal: int = 1) -> Any:
    return c.put(
        base(org) + "/rates",
        json={"currency": currency, "rate_date": d, "rate": rate, "nominal": nominal},
        headers=h(c),
    )


def cbu_payload(d: str, rows: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    day, month, year = d[8:10], d[5:7], d[:4]
    return [
        {
            "id": i,
            "Code": "000",
            "Ccy": ccy,
            "Nominal": nominal,
            "Rate": rate,
            "Diff": "0",
            "Date": f"{day}.{month}.{year}",
        }
        for i, (ccy, nominal, rate) in enumerate(rows)
    ]


@pytest.fixture
def cbu_mock() -> Iterator[dict[str, Any]]:
    """Подмена ответа cbu.uz (в тестах реальная сеть не используется)."""
    state: dict[str, Any] = {"status": 200, "body": [], "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(str(request.url))
        if isinstance(state["body"], str):
            return httpx.Response(state["status"], text=state["body"])
        return httpx.Response(state["status"], json=state["body"])

    cbu.set_client_for_tests(lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    yield state
    cbu.set_client_for_tests(None)


# ---------------- курсы вручную ----------------


def test_manual_rates_validation_lookup_and_permissions(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    enable(owner, org, "USD")
    ok = put_rate(owner, org, "usd", "2026-10-05", "12000.5")
    assert ok.status_code == 200, ok.text
    assert ok.json() | {"id": "", "created_at": ""} == {
        "id": "",
        "currency": "USD",
        "rate_date": "2026-10-05",
        "nominal": 1,
        "rate": "12000.500000",
        "source": "manual",
        "source_ref": None,
        "created_at": "",
    }
    assert put_rate(owner, org, "UZS", "2026-10-05", "1").json()["code"] == "rate_for_base_currency"
    assert (
        put_rate(owner, org, "EUR", "2026-10-05", "13000").json()["code"] == "currency_not_enabled"
    )
    for bad in ("0", "-5", "1e3", "abc", "1.1234567"):
        assert put_rate(owner, org, "USD", "2026-10-05", bad).status_code == 422, bad
    assert (
        owner.put(
            base(org) + "/rates",
            json={"currency": "USD", "rate_date": "2026-10-05", "rate": 12000.5},
            headers=h(owner),
        ).status_code
        == 422
    )  # float
    assert put_rate(owner, org, "USD", "2026-10-05", "12000", nominal=0).status_code == 422
    # перезапись того же дня и список
    put_rate(owner, org, "USD", "2026-10-05", "12100")
    put_rate(owner, org, "USD", "2026-10-01", "11900")
    lst = owner.get(base(org) + "/rates", params={"currency": "usd"}).json()
    assert [(x["rate_date"], x["rate"]) for x in lst["items"]] == [
        ("2026-10-05", "12100.000000"),
        ("2026-10-01", "11900.000000"),
    ]
    assert owner.get(base(org) + "/rates", params={"date_from": "2026-10-03"}).json()["total"] == 1
    # поиск: последний на дату или ранее
    assert (
        owner.get(
            base(org) + "/rates/lookup", params={"currency": "USD", "on": "2026-10-09"}
        ).json()["rate_date"]
        == "2026-10-05"
    )
    assert (
        owner.get(
            base(org) + "/rates/lookup", params={"currency": "USD", "on": "2026-10-03"}
        ).json()["rate_date"]
        == "2026-10-01"
    )
    nf = owner.get(base(org) + "/rates/lookup", params={"currency": "USD", "on": "2026-09-01"})
    assert nf.status_code == 404 and nf.json()["code"] == "rate_not_found"
    # права
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert auditor.get(base(org) + "/rates").status_code == 200
    assert put_rate(auditor, org, "USD", "2026-10-06", "1").status_code == 403
    assert keeper.get(base(org) + "/rates").status_code == 403
    other, org2 = setup_org(new_client, "two@example.uz")
    assert other.get(base(org) + "/rates").status_code == 404
    assert other.get(base(org2) + "/rates").json()["total"] == 0
    assert "exchange_rate.set" in actions(admin_engine)


# ---------------- курсы ЦБ ----------------


def test_cbu_import(new_client: NewClient, cbu_mock: dict[str, Any], admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    enable(owner, org, "USD")
    cbu_mock["body"] = cbu_payload(
        "2026-10-09", [("USD", "1", "11846.57"), ("EUR", "1", "13240.91"), ("RUB", "10", "748.50")]
    )
    r = owner.post(
        base(org) + "/rates/import-cbu", json={"rate_date": "2026-10-09"}, headers=h(owner)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["imported"] == ["USD"]
        and body["not_enabled"] == ["EUR", "RUB"]
        and body["skipped_existing"] == []
    )
    assert cbu_mock["calls"] == ["https://cbu.uz/ru/arkhiv-kursov-valyut/json/all/2026-10-09/"]
    row = owner.get(base(org) + "/rates").json()["items"][0]
    assert (row["currency"], row["rate"], row["source"], row["source_ref"]) == (
        "USD",
        "11846.570000",
        "cbu",
        "https://cbu.uz/ru/arkhiv-kursov-valyut/json/all/2026-10-09/",
    )
    # повтор не дублирует; ручной курс не перезаписывается
    again = owner.post(
        base(org) + "/rates/import-cbu", json={"rate_date": "2026-10-09"}, headers=h(owner)
    ).json()
    assert again["imported"] == [] and again["skipped_existing"] == ["USD"]
    enable(owner, org, "RUB")
    put_rate(owner, org, "USD", "2026-10-09", "99999")
    third = owner.post(
        base(org) + "/rates/import-cbu", json={"rate_date": "2026-10-09"}, headers=h(owner)
    ).json()
    assert third["imported"] == ["RUB"] and third["skipped_existing"] == ["USD"]
    rub = owner.get(base(org) + "/rates", params={"currency": "RUB"}).json()["items"][0]
    assert (rub["nominal"], rub["rate"]) == (10, "748.500000")
    usd = owner.get(base(org) + "/rates", params={"currency": "USD"}).json()["items"][0]
    assert (usd["source"], usd["rate"]) == ("manual", "99999.000000")
    assert "exchange_rate.imported" in actions(admin_engine)


def test_cbu_import_failures_and_guards(new_client: NewClient, cbu_mock: dict[str, Any]) -> None:
    owner, org = setup_org(new_client)
    enable(owner, org, "USD")

    def imp(d: str = "2026-10-09") -> Any:
        return owner.post(base(org) + "/rates/import-cbu", json={"rate_date": d}, headers=h(owner))

    # сервис вернул курсы ДРУГОЙ даты (так он отвечает, когда на день ничего не опубликовано) — не принимаем
    cbu_mock["body"] = cbu_payload("2026-10-08", [("USD", "1", "11800")])
    r = imp()
    assert r.status_code == 404 and r.json()["code"] == "rates_not_published"
    cbu_mock["status"] = 500
    assert imp().status_code == 502 and imp().json()["code"] == "rates_provider_unavailable"
    cbu_mock.update(status=200, body="<html>not json</html>")
    assert imp().json()["code"] == "rates_provider_invalid"
    cbu_mock["body"] = {"unexpected": "shape"}
    assert imp().json()["code"] == "rates_provider_invalid"
    cbu_mock["body"] = [{"Ccy": "USD", "Nominal": "1", "Rate": "abc", "Date": "09.10.2026"}]
    assert imp().json()["code"] == "rates_provider_invalid"
    assert imp("2999-01-01").json()["code"] == "rate_date_in_future"
    assert owner.get(base(org) + "/rates").json()["total"] == 0  # ничего не записалось
    # курсы ЦБ — к сумам: при другой базовой валюте загрузка запрещена
    usd_owner = new_client()
    register(usd_owner, "usd@example.uz")
    usd_org = create_org(usd_owner, "ООО Доллар", base_currency="USD").json()["id"]
    r2 = usd_owner.post(
        base(usd_org) + "/rates/import-cbu",
        json={"rate_date": "2026-10-09"},
        headers=csrf_headers(usd_owner),
    )
    assert r2.status_code == 422 and r2.json()["code"] == "rates_base_not_uzs"
    # право
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    assert (
        auditor.post(
            base(org) + "/rates/import-cbu",
            json={"rate_date": "2026-10-09"},
            headers=csrf_headers(auditor),
        ).status_code
        == 403
    )


# ---------------- налоговые коды ----------------


def tax(c: TestClient, org: str, code: str = "VAT-X", rate: str = "12", **extra: Any) -> Any:
    return c.post(
        base(org) + "/tax-codes",
        json={"code": code, "name": "НДС тест", "rate": rate, **extra},
        headers=h(c),
    )


def test_tax_code_lifecycle(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    assert owner.get(base(org) + "/tax-codes").json() == []  # ничего не предзаполнено
    ref = owner.post(base(org) + "/tax-codes/suggest-reference", headers=h(owner))
    assert ref.status_code == 201, ref.text
    r = ref.json()
    assert (r["code"], r["rate"], r["status"], r["is_default"]) == ("VAT-12", "12", "DRAFT", False)
    assert "Бухгалтер обязан проверить" in r["source_note"] and r["source_url"].startswith(
        "https://"
    )
    assert (
        owner.post(base(org) + "/tax-codes/suggest-reference", headers=h(owner)).json()["code"]
        == "tax_code_taken"
    )
    # основным может быть только подтверждённый
    assert (
        owner.post(base(org) + f"/tax-codes/{r['id']}/set-default", headers=h(owner)).json()["code"]
        == "tax_code_not_confirmed"
    )
    upd = owner.patch(
        base(org) + f"/tax-codes/{r['id']}",
        json={"name": "НДС 12 % (проверено)", "source_note": "Проверено по lex.uz"},
        headers=h(owner),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "НДС 12 % (проверено)"
    conf = owner.post(base(org) + f"/tax-codes/{r['id']}/confirm", headers=h(owner))
    assert (
        conf.status_code == 200
        and conf.json()["status"] == "CONFIRMED"
        and conf.json()["confirmed_by_name"]
    )
    assert (
        owner.patch(
            base(org) + f"/tax-codes/{r['id']}", json={"name": "x"}, headers=h(owner)
        ).json()["code"]
        == "tax_code_confirmed"
    )
    assert (
        owner.post(base(org) + f"/tax-codes/{r['id']}/set-default", headers=h(owner)).json()[
            "is_default"
        ]
        is True
    )
    # второй код: без основания не подтвердить; основной переключается, остаётся один
    bare = tax(owner, org, "VAT-0", "0").json()
    assert (
        owner.post(base(org) + f"/tax-codes/{bare['id']}/confirm", headers=h(owner)).json()["code"]
        == "tax_source_required"
    )
    owner.patch(
        base(org) + f"/tax-codes/{bare['id']}",
        json={"source_note": "Нулевая ставка для сельхозпроизводителей (по решению бухгалтера)"},
        headers=h(owner),
    )
    assert (
        owner.post(base(org) + f"/tax-codes/{bare['id']}/confirm", headers=h(owner)).status_code
        == 200
    )
    assert (
        owner.post(base(org) + f"/tax-codes/{bare['id']}/set-default", headers=h(owner)).status_code
        == 200
    )
    defaults = [t["code"] for t in owner.get(base(org) + "/tax-codes").json() if t["is_default"]]
    assert defaults == ["VAT-0"]
    # проверки ввода
    assert tax(owner, org, "VAT-X", "100.0001").status_code == 422
    assert tax(owner, org, "VAT-X", "-1").status_code == 422
    assert tax(owner, org, "VAT-X", "1e1").status_code == 422
    assert (
        tax(
            owner, org, "VAT-X", "12", effective_from="2026-12-01", effective_to="2026-01-01"
        ).json()["code"]
        == "invalid_range"
    )
    assert tax(owner, org, "VAT-0", "5").json()["code"] == "tax_code_taken"
    # БД: основной код обязан быть подтверждённым
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE tax_code SET status = 'DRAFT', confirmed_at = NULL, confirmed_by = NULL WHERE is_default"
            )
        )
    acts = actions(admin_engine)
    assert {
        "tax_code.created",
        "tax_code.confirmed",
        "tax_code.default_set",
        "tax_code.updated",
    } <= set(acts)
    # права
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    assert auditor.get(base(org) + "/tax-codes").status_code == 200
    assert tax(auditor, org, "VAT-Z").status_code == 403
    assert (
        auditor.post(base(org) + f"/tax-codes/{bare['id']}/confirm", headers=h(auditor)).status_code
        == 403
    )
    other, org2 = setup_org(new_client, "two@example.uz")
    assert other.get(base(org2) + "/tax-codes").json() == []
    assert other.get(base(org) + "/tax-codes").status_code == 404
    assert (
        other.post(base(org2) + f"/tax-codes/{r['id']}/confirm", headers=h(other)).status_code
        == 404
    )


def test_proposal_warns_when_tax_does_not_match_the_confirmed_vat_rate(
    new_client: NewClient,
) -> None:
    owner, org = setup_org(new_client)
    a, b, t = accounts(owner, org)
    rule = approved_rule(owner, org, a, b, t)
    good = doc(owner, org, "1120", "120")  # (1120 − 120) × 12 % = 120
    off = doc(owner, org, "1120", "50")
    assert (
        proposals(owner, org, off).json()[0]["warnings"] == []
    )  # основного НДС нет — предупреждений нет
    ref = owner.post(base(org) + "/tax-codes/suggest-reference", headers=h(owner)).json()
    owner.post(base(org) + f"/tax-codes/{ref['id']}/confirm", headers=h(owner))
    assert proposals(owner, org, off).json()[0]["warnings"] == []  # подтверждён, но не основной
    owner.post(base(org) + f"/tax-codes/{ref['id']}/set-default", headers=h(owner))
    assert proposals(owner, org, good).json()[0]["warnings"] == []
    p = proposals(owner, org, off).json()[0]
    assert (
        p["warnings"] == ["tax_rate_mismatch"] and p["problems"] == []
    )  # предупреждение не блокирует
    assert (
        owner.post(
            base(org) + "/entries/from-document",
            json={"document_id": off, "rule_id": rule["id"]},
            headers=h(owner),
        ).status_code
        == 201
    )


# ---------------- отчёт в базовой валюте и CSV ----------------


def test_trial_balance_in_base_currency(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    enable(owner, org, "USD")
    enable(owner, org, "RUB")
    cash, income = acc(owner, org, "1000", "Касса")["id"], acc(owner, org, "9000", "Доходы")["id"]
    post_entry(owner, org, "2026-10-05", [(cash, "100", "0"), (income, "0", "100")])  # UZS
    post_entry(owner, org, "2026-10-06", [(cash, "10", "0"), (income, "0", "10")], currency="USD")
    put_rate(owner, org, "USD", "2026-10-04", "12000.55")  # на 6-е действует курс 4-го
    r = owner.get(
        base(org) + "/reports/trial-balance-base",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    ).json()
    assert (
        r["currency"] == "UZS"
        and r["complete"] is True
        and r["balanced"] is True
        and r["missing_rates"] == []
    )
    by = {x["code"]: x for x in r["rows"]}
    assert (
        by["1000"]["turnover_debit"] == "120105.50" and by["1000"]["closing_debit"] == "120105.50"
    )  # 100 + 10 × 12000.55
    assert (
        by["9000"]["closing_credit"] == "120105.50"
        and r["totals"]["turnover_debit"] == r["totals"]["turnover_credit"] == "120105.50"
    )
    assert "курсовые разницы не считаются" in r["note"]
    # номинал: курс за 10 единиц
    post_entry(owner, org, "2026-10-07", [(cash, "30", "0"), (income, "0", "30")], currency="RUB")
    put_rate(owner, org, "RUB", "2026-10-07", "748.5", nominal=10)
    r2 = owner.get(
        base(org) + "/reports/trial-balance-base",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    ).json()
    assert {x["code"]: x for x in r2["rows"]}["1000"][
        "closing_debit"
    ] == "122351.00"  # + 30 × 748.5 / 10 = 2245.50
    # нет курса -> отчёт неполный, с перечнем недостающих; проводки не теряются молча
    post_entry(owner, org, "2026-10-08", [(cash, "5", "0"), (income, "0", "5")], currency="USD")
    put_rate(owner, org, "USD", "2026-10-04", "12000.55")  # курс есть; нужен случай «нет вообще»
    enable(owner, org, "EUR")
    post_entry(owner, org, "2026-10-09", [(cash, "1", "0"), (income, "0", "1")], currency="EUR")
    r3 = owner.get(
        base(org) + "/reports/trial-balance-base",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    ).json()
    assert (
        r3["complete"] is False
        and r3["balanced"] is False
        and r3["missing_rates"] == [{"currency": "EUR", "rate_date": "2026-10-09"}]
    )
    # входящее сальдо: до периода
    r4 = owner.get(
        base(org) + "/reports/trial-balance-base",
        params={"date_from": "2026-10-06", "date_to": "2026-10-31"},
    ).json()
    assert {x["code"]: x for x in r4["rows"]}["1000"]["opening_debit"] == "100.00"
    assert (
        owner.get(
            base(org) + "/reports/trial-balance-base",
            params={"date_from": "2026-10-31", "date_to": "2026-10-01"},
        ).status_code
        == 422
    )
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    assert (
        auditor.get(
            base(org) + "/reports/trial-balance-base",
            params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
        ).status_code
        == 200
    )
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert (
        keeper.get(
            base(org) + "/reports/trial-balance-base",
            params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
        ).status_code
        == 403
    )


def test_trial_balance_csv_export(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    evil = acc(owner, org, "=1+1", "=cmd|' /C calc'!A0")["id"]
    other = acc(owner, org, "2000", "Нормальный")["id"]
    post_entry(owner, org, "2026-10-05", [(evil, "10", "0"), (other, "0", "10")])
    r = owner.get(
        base(org) + "/reports/trial-balance.csv",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
    )
    assert r.status_code == 200
    assert (
        r.headers["content-type"].startswith("text/csv")
        and "attachment" in r.headers["content-disposition"]
    )
    assert (
        r.headers["cache-control"] == "private, no-store"
        and r.headers["x-content-type-options"] == "nosniff"
    )
    body = r.content.decode("utf-8")
    assert body.startswith("﻿")
    lines = body.lstrip("﻿").splitlines()
    assert lines[0].startswith("Оборотно-сальдовая ведомость 2026-10-01 — 2026-10-31, валюта UZS")
    assert (
        "'=1+1;'=cmd|' /C calc'!A0;0.00;0.00;10.00;0.00;10.00;0.00" in lines[2]
    )  # формулы обезврежены апострофом
    assert lines[-1].startswith(";Итого;") and lines[-1].count("10.00") == 4
    rb = owner.get(
        base(org) + "/reports/trial-balance.csv",
        params={"date_from": "2026-10-01", "date_to": "2026-10-31", "base": "true"},
    )
    assert rb.status_code == 200 and "валюта UZS" in rb.text
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    assert (
        keeper.get(
            base(org) + "/reports/trial-balance.csv",
            params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
        ).status_code
        == 403
    )


def test_rls_on_reference_tables(new_client: NewClient, app_engine: Engine) -> None:
    a_owner, org_a = setup_org(new_client, "a@example.uz")
    b_owner, org_b = setup_org(new_client, "b@example.uz")
    for c, org in ((a_owner, org_a), (b_owner, org_b)):
        enable(c, org, "USD")
        put_rate(c, org, "USD", "2026-10-05", "12000")
        tax(c, org)
    tables = ("exchange_rate", "tax_code")
    with app_engine.connect() as conn:
        for t in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for t in tables:
            assert {r[0] for r in conn.execute(text(f"SELECT organization_id::text FROM {t}"))} == {
                org_a
            }, t
