from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.ai_helpers import good_payload, run_job
from tests.files import pdf
from tests.helpers import create_org, csrf_headers, register, upload_doc

NewClient = Callable[[], TestClient]


def _ctx(conn: Any, org: str = "") -> None:
    conn.execute(
        text(
            "SELECT set_config('app.current_org', :o, true), set_config('app.current_user', '', true)"
        ),
        {"o": org},
    )


def _seed(new_client: NewClient, mock_ai: Any) -> tuple[str, str, str, str]:
    mock_ai(good_payload())
    orgs: list[str] = []
    docs: list[str] = []
    for email in ("a@example.uz", "b@example.uz"):
        c = new_client()
        register(c, email)
        org = create_org(c, "Org").json()["id"]
        did = upload_doc(c, org, pdf(f"sec-ai-{email}")).json()["id"]
        job = c.post(
            f"/api/v1/organizations/{org}/documents/{did}/extract", headers=csrf_headers(c)
        ).json()["extraction"]["job"]["id"]
        run_job(job, org)
        orgs.append(org)
        docs.append(did)
    return orgs[0], orgs[1], docs[0], docs[1]


def test_rls_on_ai_tables(new_client: NewClient, mock_ai: Any, app_engine: Engine) -> None:
    org_a, _, _, _ = _seed(new_client, mock_ai)
    tables = ("ai_job", "ai_result", "extracted_field")
    with app_engine.connect() as c:
        for t in tables:
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as c:
        _ctx(c, org_a)
        for t in tables:
            assert {r[0] for r in c.execute(text(f"SELECT organization_id::text FROM {t}"))} == {
                org_a
            }, t


def test_foreign_writes_and_deletes_blocked(
    new_client: NewClient, mock_ai: Any, app_engine: Engine
) -> None:
    org_a, org_b, _, doc_b = _seed(new_client, mock_ai)
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert (
            c.execute(
                text("UPDATE ai_job SET status='failed' WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_b},
            ).rowcount
            == 0
        )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO ai_job (id, organization_id, document_id, status, provider, model, prompt_version, requested_by) "
                    "SELECT gen_random_uuid(), CAST(:o AS uuid), CAST(:d AS uuid), 'queued', 'mock', 'm', 'v1', id FROM users LIMIT 1"
                ),
                {"o": org_b, "d": doc_b},
            )
    for table in (
        "ai_job",
        "ai_result",
        "extracted_field",
    ):  # история распознаваний неизменяема и неудаляема для приложения
        with app_engine.connect() as c:
            _ctx(c, org_a)
            with pytest.raises(DBAPIError):
                c.execute(text(f"DELETE FROM {table}"))
    for _table, sql in (
        ("ai_result", "UPDATE ai_result SET matches = '{}'"),
        ("extracted_field", "UPDATE extracted_field SET value = 'x'"),
    ):
        with app_engine.connect() as c:
            _ctx(c, org_a)
            with pytest.raises(DBAPIError):
                c.execute(text(sql))


def test_composite_fks_and_constraints(
    new_client: NewClient, mock_ai: Any, admin_engine: Engine
) -> None:
    org_a, org_b, doc_a, doc_b = _seed(new_client, mock_ai)
    with admin_engine.connect() as c:
        me = c.execute(
            text("SELECT created_by FROM document WHERE id = CAST(:d AS uuid)"), {"d": doc_a}
        ).scalar_one()
        job_a = c.execute(
            text("SELECT id FROM ai_job WHERE organization_id = CAST(:o AS uuid)"), {"o": org_a}
        ).scalar_one()
    ins = (
        "INSERT INTO ai_job (id, organization_id, document_id, status, provider, model, prompt_version, requested_by) "
        "VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:d AS uuid), :s, 'mock', 'm', 'v1', :u)"
    )
    with (
        admin_engine.begin() as c,
        pytest.raises(IntegrityError),
    ):  # задача организации A на документ организации B
        c.execute(text(ins), {"o": org_a, "d": doc_b, "s": "succeeded", "u": me})
    with admin_engine.begin() as c, pytest.raises(IntegrityError):  # недопустимый статус
        c.execute(text(ins), {"o": org_a, "d": doc_a, "s": "bogus", "u": me})
    # на документ не больше одной активной задачи
    with admin_engine.begin() as c:
        c.execute(text(ins), {"o": org_a, "d": doc_a, "s": "queued", "u": me})
    with admin_engine.begin() as c, pytest.raises(IntegrityError):
        c.execute(text(ins), {"o": org_a, "d": doc_a, "s": "running", "u": me})
    with admin_engine.begin() as c:  # завершённых может быть сколько угодно
        c.execute(text(ins), {"o": org_a, "d": doc_a, "s": "failed", "u": me})
    bad_field = (
        "INSERT INTO extracted_field (id, organization_id, document_id, job_id, path, value, confidence, validation) "
        "VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:d AS uuid), CAST(:j AS uuid), :p, 'x', :c, :v)"
    )
    for conf, val in ((1.5, "ok"), (0.5, "weird")):
        with admin_engine.begin() as c, pytest.raises(IntegrityError):
            c.execute(
                text(bad_field),
                {"o": org_a, "d": doc_a, "j": str(job_a), "p": f"p{conf}", "c": conf, "v": val},
            )
    with (
        admin_engine.begin() as c,
        pytest.raises(IntegrityError),
    ):  # поле чужой организации к задаче этой
        c.execute(
            text(bad_field),
            {"o": org_b, "d": doc_b, "j": str(job_a), "p": "x", "c": 0.5, "v": "ok"},
        )


def test_runtime_table_is_single_row_without_secrets(admin_engine: Engine, clean_db: None) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ai_runtime (id, provider, model, configured) VALUES (1, 'x', 'y', true)"
            )
        )
    with admin_engine.begin() as c, pytest.raises(IntegrityError):
        c.execute(
            text(
                "INSERT INTO ai_runtime (id, provider, model, configured) VALUES (2, 'x', 'y', true)"
            )
        )
    with admin_engine.connect() as c:
        cols = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='ai_runtime'"
                )
            )
        }
    assert cols == {"id", "provider", "model", "configured", "updated_at"}  # ключей в БД нет
