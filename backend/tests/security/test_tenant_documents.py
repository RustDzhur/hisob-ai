from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.files import pdf
from tests.helpers import csrf_headers, setup_org, upload_doc

NewClient = Callable[[], TestClient]


def _ctx(conn: Any, org: str = "") -> None:
    conn.execute(
        text(
            "SELECT set_config('app.current_org', :o, true), set_config('app.current_user', '', true)"
        ),
        {"o": org},
    )


def _seed(new_client: NewClient) -> tuple[str, str, str, str, TestClient]:
    ids: list[str] = []
    orgs: list[str] = []
    last: TestClient | None = None
    for email in ("a@example.uz", "b@example.uz"):
        c, org = setup_org(new_client, email)
        cp = c.post(
            f"/api/v1/organizations/{org}/counterparties",
            json={"name": "Контрагент"},
            headers=csrf_headers(c),
        ).json()["id"]
        d = upload_doc(c, org, pdf(f"sec-{email}")).json()
        c.patch(
            f"/api/v1/organizations/{org}/documents/{d['id']}",
            json={
                "expected_version": 1,
                "counterparty_id": cp,
                "lines": [{"description": "Товар"}],
            },
            headers=csrf_headers(c),
        )
        ids.append(d["id"])
        orgs.append(org)
        last = c
    assert last is not None
    return orgs[0], orgs[1], ids[0], ids[1], last


def test_rls_documents_files_lines(new_client: NewClient, app_engine: Engine) -> None:
    org_a, _, _, _, _ = _seed(new_client)
    with app_engine.connect() as c:
        for t in ("document", "document_file", "document_line"):
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as c:
        _ctx(c, org_a)
        for t in ("document", "document_file", "document_line"):
            assert {r[0] for r in c.execute(text(f"SELECT organization_id::text FROM {t}"))} == {
                org_a
            }, t


def test_rls_blocks_foreign_writes_and_deletes(new_client: NewClient, app_engine: Engine) -> None:
    org_a, org_b, _, doc_b, _ = _seed(new_client)
    with app_engine.connect() as c:
        _ctx(c, org_a)
        assert (
            c.execute(
                text("UPDATE document SET notes='x' WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_b},
            ).rowcount
            == 0
        )
        assert (
            c.execute(
                text("DELETE FROM document_line WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_b},
            ).rowcount
            == 0
        )
    with app_engine.connect() as c:
        _ctx(c, org_a)
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO document_line (id, organization_id, document_id, position, description) VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:d AS uuid), 99, 'x')"
                ),
                {"o": org_b, "d": doc_b},
            )
    for table in (
        "document",
        "document_file",
    ):  # удалять документы и файлы приложению нельзя вообще
        with app_engine.connect() as c:
            _ctx(c, org_a)
            with pytest.raises(DBAPIError):
                c.execute(text(f"DELETE FROM {table}"))


def test_composite_foreign_keys_block_cross_org_links(
    new_client: NewClient, admin_engine: Engine
) -> None:
    org_a, org_b, doc_a, doc_b, _ = _seed(new_client)
    with admin_engine.connect() as conn:
        cp_b = conn.execute(
            text("SELECT id FROM counterparty WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org_b},
        ).scalar_one()
    # документ организации A нельзя связать с контрагентом организации B, даже суперпользователем
    with admin_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "UPDATE document SET counterparty_id = CAST(:c AS uuid) WHERE id = CAST(:d AS uuid)"
            ),
            {"c": str(cp_b), "d": doc_a},
        )
    # и строку документа B — с товаром/документом другой организации
    with admin_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO document_line (id, organization_id, document_id, position, description) VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:d AS uuid), 50, 'x')"
            ),
            {"o": org_a, "d": doc_b},
        )
    with admin_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO document_file (id, organization_id, document_id, sha256, size_bytes, content_type, original_name, storage_key) VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:d AS uuid), 'abc', 1, 'application/pdf', 'x.pdf', 'k')"
            ),
            {"o": org_a, "d": doc_b},
        )


def test_db_constraints(new_client: NewClient, admin_engine: Engine) -> None:
    _, _, doc_a, _, _ = _seed(new_client)
    bad_updates = [
        "UPDATE document SET status = 'WHATEVER'",
        "UPDATE document SET doc_type = 'selfie'",
        "UPDATE document SET status = 'REJECTED'",  # без причины
        "UPDATE document SET rejection_reason = 'x'",  # причина без статуса REJECTED
        "UPDATE document SET status = 'DUPLICATE'",  # без ссылки на оригинал
        "UPDATE document SET total_amount = -1",
    ]
    for sql in bad_updates:
        with admin_engine.begin() as conn, pytest.raises(IntegrityError):
            conn.execute(text(sql + " WHERE id = CAST(:d AS uuid)"), {"d": doc_a})
    with (
        admin_engine.begin() as conn,
        pytest.raises(IntegrityError),
    ):  # отрицательное количество / пустое описание
        conn.execute(
            text("UPDATE document_line SET quantity = -1 WHERE document_id = CAST(:d AS uuid)"),
            {"d": doc_a},
        )
    with admin_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "UPDATE document_line SET description = '  ' WHERE document_id = CAST(:d AS uuid)"
            ),
            {"d": doc_a},
        )


def test_same_hash_unique_only_within_org(new_client: NewClient, admin_engine: Engine) -> None:
    org_a, _org_b, doc_a, _, _ = _seed(new_client)
    with admin_engine.connect() as conn:
        sha = conn.execute(
            text("SELECT sha256 FROM document_file WHERE document_id = CAST(:d AS uuid)"),
            {"d": doc_a},
        ).scalar_one()
    me = None
    with admin_engine.connect() as conn:
        me = conn.execute(
            text("SELECT created_by FROM document WHERE id = CAST(:d AS uuid)"), {"d": doc_a}
        ).scalar_one()
        new_doc = conn.execute(text("SELECT gen_random_uuid()")).scalar_one()
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO document (id, organization_id, doc_type, status, created_by) VALUES (:i, CAST(:o AS uuid), 'other', 'NEEDS_REVIEW', :u)"
            ),
            {"i": new_doc, "o": org_a, "u": me},
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO document_file (id, organization_id, document_id, sha256, size_bytes, content_type, original_name, storage_key) VALUES (gen_random_uuid(), CAST(:o AS uuid), :d, :s, 1, 'application/pdf', 'x.pdf', 'k2')"
                ),
                {"o": org_a, "d": new_doc, "s": sha},
            )
