"""Этап 9.1: защитные заголовки, ограничение частоты запросов, аудит выгрузок."""

import json
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from app.platform import ratelimit
from app.platform.config import get_settings
from tests.helpers import register, setup_org
from tests.integration.test_bank import NewClient, with_profile
from tests.integration.test_bank_reconcile import load


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[ratelimit.MemoryStore]:
    store = ratelimit.MemoryStore()
    ratelimit.set_store_for_tests(store)
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    yield store
    ratelimit.set_store_for_tests(None)


def rule(
    name: str, limit: int, by: str = "actor", method: str = "*", pattern: str = r"^/api/"
) -> ratelimit.Rule:
    return ratelimit.Rule(name, method, re.compile(pattern), limit, 60, by)


def test_security_headers_on_api(new_client: NewClient) -> None:
    c = new_client()
    r = c.get("/api/v1/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-correlation-id"]


def test_rate_limit_blocks_and_counts_per_actor(
    new_client: NewClient, limiter: ratelimit.MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ratelimit, "RULES", [rule("general", 3)])
    a, b = new_client(), new_client()
    codes = [a.get("/api/v1/auth/me").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]  # 401 — не вошли, но запросы считаются
    blocked = a.get("/api/v1/auth/me")
    assert blocked.status_code == 429 and blocked.json()["code"] == "rate_limited"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    assert blocked.headers["x-correlation-id"]
    # у вошедшего пользователя своё окно (ключ — cookie сессии), а не общий адрес
    register(b, "limited@example.uz")
    assert b.get("/api/v1/auth/me").status_code in (200, 429)


def body(n: int) -> dict[str, str]:
    return {"email": f"u{n}@example.uz", "password": "correct horse battery", "full_name": "Тест"}


def test_rate_limit_by_ip_rule_and_disabled_flag(
    new_client: NewClient, limiter: ratelimit.MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ratelimit, "RULES", [rule("register", 2, "ip", "POST", r"^/api/v1/auth/register$")]
    )
    c = new_client()
    assert c.post("/api/v1/auth/register", json=body(1)).status_code == 201
    other = new_client()
    assert other.post("/api/v1/auth/register", json=body(2)).status_code == 201
    third = new_client().post("/api/v1/auth/register", json=body(3))
    assert third.status_code == 429 and third.json()["code"] == "rate_limited"
    # остальные пути правилом не затронуты
    assert c.get("/api/v1/auth/me").status_code == 200
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    assert new_client().post("/api/v1/auth/register", json=body(4)).status_code == 201


def test_rate_limit_fails_open_when_store_is_down(
    new_client: NewClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def hit(self, key: str, window: int) -> int:
            raise ConnectionError("redis down")

    ratelimit.set_store_for_tests(Broken())
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    try:
        assert new_client().get("/api/v1/auth/me").status_code == 401  # не 500 и не 429
    finally:
        ratelimit.set_store_for_tests(None)


def test_default_rules_cover_sensitive_paths() -> None:
    names = {r.name for r in ratelimit.RULES}
    assert {"register", "login", "invitation", "upload", "export", "general"} <= names
    paths = [
        ("POST", "/api/v1/organizations/x/documents", "upload"),
        ("POST", "/api/v1/organizations/x/bank/accounts/y/imports", "upload"),
        ("GET", "/api/v1/organizations/x/bank/reports/summary.csv", "export"),
        ("POST", "/api/v1/auth/login", "login"),
    ]
    for method, path, name in paths:
        assert any(
            r.name == name and r.method in ("*", method) and r.pattern.search(path)
            for r in ratelimit.RULES
        ), (method, path)


def test_exports_are_audited_without_content(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    acc = with_profile(owner, org)
    load(owner, org, acc["id"], "05.10.2026;777,00;Секретное назначение;;;")
    q = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    base = f"/api/v1/organizations/{org}"
    for path in (
        "/bank/reports/summary.csv",
        "/bank/reports/matches.csv",
        "/bank/reports/transactions.csv",
        "/accounting/reports/trial-balance.csv",
    ):
        r = owner.get(base + path, params=q)
        assert r.status_code == 200, (path, r.text)
    with admin_engine.connect() as c:
        rows = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT after FROM audit_event WHERE action = 'export.created' ORDER BY occurred_at"
                )
            )
        ]
    reports = [json.loads(r) if isinstance(r, str) else r for r in rows]
    assert [x["report"] for x in reports] == [
        "bank_summary",
        "bank_matches",
        "bank_transactions",
        "trial_balance",
    ]
    assert (
        reports[2]["rows"] == 1
        and reports[2]["date_from"] == "2026-10-01"
        and reports[0]["format"] == "csv"
    )
    assert "Секретное назначение" not in json.dumps(reports) and "777" not in json.dumps(reports)
    # без права доступа выгрузки нет и в аудит она не попадает
    keeper_rows = _count(admin_engine)
    assert keeper_rows == 4


def _count(engine: Engine) -> int:
    with engine.connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM audit_event WHERE action = 'export.created'")
            ).scalar()
            or 0
        )


def test_metrics_endpoint_is_protected_and_uses_route_templates(
    new_client: NewClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = new_client()
    assert c.get("/metrics").status_code == 404  # токен не задан — метрик нет
    monkeypatch.setattr(get_settings(), "metrics_token", "t0ken-for-tests")
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    owner, org = setup_org(new_client)
    owner.get(f"/api/v1/organizations/{org}/bank/accounts")
    r = c.get("/metrics", headers={"Authorization": "Bearer t0ken-for-tests"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text_ = r.text
    assert (
        "hisob_http_requests_total" in text_
        and "hisob_http_request_duration_seconds_bucket" in text_
    )
    assert (
        'route="/organizations/{org_id}/bank/accounts"' in text_
        or 'route="/api/v1/organizations/{org_id}/bank/accounts"' in text_
    )  # шаблон, не конкретный id
    assert org not in text_  # идентификаторы организаций в метки не попадают


def test_ready_reports_database(new_client: NewClient) -> None:
    c = new_client()
    assert c.get("/ready").json() == {"status": "ready"}
    assert c.get("/health").json() == {"status": "ok"}
