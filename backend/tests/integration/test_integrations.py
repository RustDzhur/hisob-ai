"""Этап 7: интеграции — честные статусы, тестовый адаптер, журнал, повторяемость, права, изоляция."""

import threading
import uuid
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import csrf_headers, join, setup_org
from tests.integration.test_bank import NewClient


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/integrations"


def provs(c: Any, org: str) -> dict[str, dict[str, Any]]:
    r = c.get(base(org))
    assert r.status_code == 200, r.text
    return {p["code"]: p for p in r.json()}


def post(c: Any, org: str, path: str, **body: Any) -> Any:
    return c.post(base(org) + path, json=body or None, headers=csrf_headers(c))


def test_registry_is_honest(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    p = provs(owner, org)
    for code in ("multibank", "faktura_uz", "didox"):
        assert p[code]["status"] == "NOT_CONFIGURED"
        assert p[code]["docs_received"] is False
        assert p[code]["capabilities"] == [] and p[code]["can_sync"] is False
        # подключить нельзя: нет адаптера
        assert post(owner, org, f"/{code}/enable").json()["code"] == "integration_not_configured"
        assert (
            post(owner, org, f"/{code}/sync", request_id=str(uuid.uuid4())).json()["code"]
            == "integration_not_configured"
        )
    assert p["mock"]["status"] == "MOCK" and p["mock"]["can_sync"] is True
    assert p["generic_file"]["status"] == "AVAILABLE"
    assert post(owner, org, "/nosuch/enable").status_code == 404


def test_mock_sync_cursor_idempotency_and_journal(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    rid = str(uuid.uuid4())
    # без включения синхронизации нет
    assert post(owner, org, "/mock/sync", request_id=rid).json()["code"] == "integration_disabled"
    assert post(owner, org, "/mock/enable").status_code == 200
    assert provs(owner, org)["mock"]["enabled"] is True
    r1 = post(owner, org, "/mock/sync", request_id=rid)
    assert r1.status_code == 200, r1.text
    run = r1.json()
    assert run["mode"] == "MOCK" and run["status"] == "SUCCEEDED"
    assert (run["records_count"], run["cursor_before"], run["cursor_after"]) == (3, None, "3")
    # повтор того же запроса — тот же запуск, курсор не сдвинулся
    r2 = post(owner, org, "/mock/sync", request_id=rid)
    assert r2.json()["id"] == run["id"]
    assert provs(owner, org)["mock"]["sync_cursor"] == "3"
    # новые запросы двигают курсор до конца, затем 0 записей
    seen = []
    for _ in range(4):
        seen.append(post(owner, org, "/mock/sync", request_id=str(uuid.uuid4())).json())
    assert [s["records_count"] for s in seen] == [3, 3, 0, 0]
    assert provs(owner, org)["mock"]["sync_cursor"] == "9"
    journal = owner.get(base(org) + "/runs", params={"provider": "mock"}).json()
    assert len(journal) == 5 and journal[0]["created_by_name"]
    # выключение сохраняет журнал, запуск снова невозможен
    assert post(owner, org, "/mock/disable").json()["enabled"] is False
    assert (
        post(owner, org, "/mock/sync", request_id=str(uuid.uuid4())).json()["code"]
        == "integration_disabled"
    )
    assert len(owner.get(base(org) + "/runs").json()) == 5


def test_parallel_syncs_do_not_lose_cursor(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    post(owner, org, "/mock/enable")
    results: list[int] = []

    def go() -> None:
        results.append(post(owner, org, "/mock/sync", request_id=str(uuid.uuid4())).status_code)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200, 200]
    runs = owner.get(base(org) + "/runs").json()
    assert sorted(r["cursor_after"] for r in runs) == ["3", "6", "9"]
    assert provs(owner, org)["mock"]["sync_cursor"] == "9"


def test_permissions_and_isolation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    post(owner, org, "/mock/enable")
    auditor = join(new_client, owner, org, "aud@example.uz", "auditor")
    accountant = join(new_client, owner, org, "acc@example.uz", "accountant")
    keeper = join(new_client, owner, org, "k@example.uz", "storekeeper")
    for c in (auditor, accountant):
        assert c.get(base(org)).status_code == 200
        assert c.get(base(org) + "/runs").status_code == 200
        assert post(c, org, "/mock/enable").status_code == 403
        assert post(c, org, "/mock/sync", request_id=str(uuid.uuid4())).status_code == 403
    assert keeper.get(base(org)).status_code == 403
    other, org_b = setup_org(new_client, "b@example.uz")
    assert other.get(base(org)).status_code in (403, 404)
    assert provs(other, org_b)["mock"]["enabled"] is False
    assert other.get(base(org_b) + "/runs").json() == []


def test_rls_and_append_only_runs(new_client: NewClient, app_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    post(owner, org, "/mock/enable")
    post(owner, org, "/mock/sync", request_id=str(uuid.uuid4()))
    with app_engine.connect() as conn:
        for tbl in ("integration_connection", "integration_sync_run"):
            assert conn.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() == 0  # без контекста
        conn.execute(text("SELECT set_config('app.current_org', :o, false)"), {"o": org})
        assert conn.execute(text("SELECT count(*) FROM integration_sync_run")).scalar() == 1
        try:
            conn.execute(text("UPDATE integration_sync_run SET records_count = 99"))
            raise AssertionError("журнал запусков должен быть неизменяемым")
        except DBAPIError:
            conn.rollback()
