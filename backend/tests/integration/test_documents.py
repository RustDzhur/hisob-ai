import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.files import jpeg_bytes, pdf, png_bytes, xlsx_bytes
from tests.helpers import csrf_headers, join, setup_org, upload_doc

NewClient = Callable[[], TestClient]


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/documents"


def get(c: TestClient, org: str, did: str) -> dict[str, Any]:
    r = c.get(f"{base(org)}/{did}")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def patch(c: TestClient, org: str, did: str, version: int | None = None, **body: Any) -> Any:
    if version is None:
        version = get(c, org, did)["version"]
    return c.patch(
        f"{base(org)}/{did}", json={"expected_version": version, **body}, headers=csrf_headers(c)
    )


def act(
    c: TestClient, org: str, did: str, action: str, version: int | None = None, **body: Any
) -> Any:
    if version is None:
        version = get(c, org, did)["version"]
    return c.post(
        f"{base(org)}/{did}/{action}",
        json={"expected_version": version, **body},
        headers=csrf_headers(c),
    )


def new_counterparty(
    c: TestClient, org: str, name: str = "ООО Поставщик", tin: str | None = "123456789"
) -> str:
    r = c.post(
        f"/api/v1/organizations/{org}/counterparties",
        json={"name": name, "tin": tin},
        headers=csrf_headers(c),
    )
    assert r.status_code == 201, r.text
    cid: str = r.json()["id"]
    return cid


def valid_fields(cp: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "doc_type": "invoice",
        "number": "N-1",
        "doc_date": "2026-10-01",
        "counterparty_id": cp,
        "currency": "UZS",
        "total_amount": "200",
        "lines": [{"description": "Товар", "quantity": "2", "unit_price": "100", "amount": "200"}],
    }
    body.update(over)
    return body


def ready_doc(c: TestClient, org: str, n: int = 0, cp: str | None = None, **over: Any) -> str:
    """Загружает файл и заполняет документ так, чтобы он проходил проверки без ошибок и предупреждений."""
    r = upload_doc(c, org, pdf(f"ready-{n}-{org}"))
    assert r.status_code == 201, r.text
    did: str = r.json()["id"]
    cp = cp or new_counterparty(c, org, name=f"Поставщик {n}", tin=f"{100000000 + n}")
    assert patch(c, org, did, **valid_fields(cp, number=f"N-{n}", **over)).status_code == 200
    return did


def actions(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return [
            r[0]
            for r in conn.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


# ---------------- права ----------------

CAN = {  # роль: (читает, загружает, правит, утверждает)
    "owner": (True, True, True, True),
    "org_admin": (True, True, True, True),
    "accountant": (True, True, True, True),
    "document_operator": (True, True, True, False),
    "sales_purchase_manager": (True, True, True, False),
    "storekeeper": (True, True, False, False),
    "auditor": (True, False, False, False),
}


@pytest.mark.parametrize("role", list(CAN))
def test_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    seed = upload_doc(owner, org, pdf(f"seed-{role}")).json()["id"]
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_upload, can_edit, can_approve = CAN[role]
    assert (actor.get(base(org)).status_code == 200) is can_read
    assert (actor.get(f"{base(org)}/{seed}").status_code == 200) is can_read
    assert (actor.get(f"{base(org)}/{seed}/file").status_code == 200) is can_read
    assert upload_doc(actor, org, pdf(f"mine-{role}")).status_code == (201 if can_upload else 403)
    assert patch(actor, org, seed, notes="x").status_code == (200 if can_edit else 403)
    ready = ready_doc(owner, org, n=1)
    assert act(actor, org, ready, "approve").status_code == (200 if can_approve else 403)


# ---------------- загрузка и файл ----------------


def test_upload_stores_file_and_download_returns_same_bytes(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    data = png_bytes(color="red")
    r = upload_doc(owner, org, data, name="../../Счёт №5.PNG", doc_type="act")
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "NEEDS_REVIEW" and d["doc_type"] == "act" and d["version"] == 1
    assert d["file"]["original_name"] == "Счёт №5.PNG"  # путь отброшен
    assert d["file"]["content_type"] == "image/png" and d["file"]["size_bytes"] == len(data)
    assert d["file"]["inline"] is True and d["created_by_name"] == "Test User"

    dl = owner.get(f"{base(org)}/{d['id']}/file")
    assert dl.status_code == 200 and dl.content == data
    assert (
        dl.headers["content-type"] == "image/png"
        and dl.headers["x-content-type-options"] == "nosniff"
    )
    assert (
        dl.headers["content-disposition"].startswith("inline;")
        and "UTF-8''" in dl.headers["content-disposition"]
    )
    assert (
        "sandbox" in dl.headers["content-security-policy"]
        and dl.headers["cache-control"] == "private, no-store"
    )
    forced = owner.get(f"{base(org)}/{d['id']}/file", params={"download": "true"})
    assert forced.headers["content-disposition"].startswith("attachment;")

    from app.platform.storage import get_storage

    with admin_engine.connect() as c:
        key, sha = c.execute(text("SELECT storage_key, sha256 FROM document_file")).one()
    assert (
        get_storage().exists(key)
        and sha == d["file"]["sha256"]
        and sha not in d["file"]["original_name"]
    )
    assert str(org) in key and "Счёт" not in key  # имя файла в ключ не попадает
    acts = actions(admin_engine)
    assert "document.uploaded" in acts and "document.file_downloaded" in acts


def test_spreadsheet_is_served_as_attachment(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    d = upload_doc(owner, org, xlsx_bytes(), name="book.xlsx").json()
    assert d["file"]["inline"] is False
    assert (
        owner.get(f"{base(org)}/{d['id']}/file")
        .headers["content-disposition"]
        .startswith("attachment;")
    )
    csv = upload_doc(owner, org, "номер;сумма\n1;100\n".encode(), name="data.csv").json()
    assert csv["file"]["content_type"] == "text/csv"


def test_upload_rejections(new_client: NewClient, monkeypatch: pytest.MonkeyPatch) -> None:
    owner, org = setup_org(new_client)
    anon = new_client()
    assert upload_doc(anon, org, pdf(1)).status_code == 401
    no_csrf = owner.post(base(org), files={"file": ("a.pdf", pdf(2), "application/pdf")})
    assert no_csrf.status_code == 403

    def code(r: Any) -> str:
        return str(r.json()["details"][0]["code"])

    assert code(upload_doc(owner, org, png_bytes(), name="fake.pdf")) == "file_type_mismatch"
    assert (
        code(upload_doc(owner, org, b"MZ\x90\x00 exe", name="run.exe")) == "file_type_unsupported"
    )
    assert code(upload_doc(owner, org, b"", name="empty.pdf")) == "file_empty"
    assert code(upload_doc(owner, org, pdf(3) + b"/JavaScript (x)", name="js.pdf")) == "file_unsafe"
    assert (
        code(upload_doc(owner, org, xlsx_bytes({"xl/vbaProject.bin": b"x"}), name="m.xlsx"))
        == "file_unsafe"
    )
    bad_type = upload_doc(owner, org, pdf(4), doc_type="selfie")
    assert bad_type.status_code == 422 and code(bad_type) == "invalid"
    assert owner.get(base(org)).json()["total"] == 0  # ни один отклонённый файл не создал документ

    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    big = upload_doc(owner, org, pdf(5) + b"0" * 2000)
    assert big.status_code == 413 and code(big) == "file_too_large"
    assert big.json()["details"][0]["params"] == {"max_mb": 0}


# ---------------- дубликаты ----------------


def test_duplicate_file_by_hash(new_client: NewClient, admin_engine: Engine) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    data = pdf("dup")
    first = upload_doc(a, org_a, data, name="one.pdf").json()
    dup = upload_doc(a, org_a, data, name="renamed.pdf")  # то же содержимое под другим именем
    assert dup.status_code == 409 and dup.json()["code"] == "document_duplicate"
    p = dup.json()["details"][0]["params"]
    assert p["document_id"] == first["id"] and p["status"] == "NEEDS_REVIEW"
    assert a.get(base(org_a)).json()["total"] == 1
    assert "document.duplicate_upload" in actions(admin_engine)
    assert upload_doc(b, org_b, data).status_code == 201  # другая организация — не дубликат

    act(a, org_a, first["id"], "archive")
    assert upload_doc(a, org_a, data).status_code == 409  # архивный документ тоже занимает хэш


def test_concurrent_identical_uploads_create_one_document(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    data = pdf("race")
    from app.main import app

    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org),
                files={"file": ("r.pdf", data, "application/pdf")},
                data={"doc_type": "invoice"},
                headers=h,
            )
            out.append(r.status_code)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM document")).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM document_file")).scalar_one() == 1


# ---------------- редактирование ----------------


def test_update_header_and_lines_roundtrip(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    cp = new_counterparty(owner, org)
    did = upload_doc(owner, org, pdf("u1")).json()["id"]
    r = patch(
        owner,
        org,
        did,
        **valid_fields(
            cp,
            number="  INV-7 ",
            total_amount="248",
            tax_total="48",
            notes="  проверить ",
            lines=[
                {
                    "description": "Молоко",
                    "quantity": "2.5",
                    "unit_price": "80",
                    "discount": "0",
                    "tax_amount": "24",
                    "amount": "200",
                },
                {
                    "description": "Услуга",
                    "quantity": "1",
                    "unit_price": "0.5",
                    "tax_amount": "24",
                    "amount": "0.5",
                },
            ],
        ),
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["version"] == 2 and d["number"] == "INV-7" and d["notes"] == "проверить"
    assert d["total_amount"] == "248.00" and d["tax_total"] == "48.00" and d["currency"] == "UZS"
    assert d["counterparty"]["name"] == "ООО Поставщик" and d["counterparty"]["tin"] == "123456789"
    assert [
        (ln["position"], ln["quantity"], ln["unit_price"], ln["amount"]) for ln in d["lines"]
    ] == [
        (1, "2.5", "80.00", "200.00"),
        (2, "1", "0.50", "0.50"),
    ]
    with admin_engine.connect() as c:
        before, after = c.execute(
            text("SELECT before, after FROM audit_event WHERE action='document.updated'")
        ).one()
    assert before["number"] is None and after["number"] == "INV-7" and len(after["lines"]) == 2

    # строки заменяются целиком; не переданные поля остаются
    d2 = patch(owner, org, did, lines=[{"description": "Только одна"}]).json()
    assert d2["version"] == 3 and len(d2["lines"]) == 1 and d2["number"] == "INV-7"
    # повтор без изменений не поднимает версию и не пишет аудит
    n = actions(admin_engine).count("document.updated")
    same = patch(owner, org, did, number="INV-7")
    assert same.json()["version"] == 3 and actions(admin_engine).count("document.updated") == n


def test_optimistic_concurrency(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    did = upload_doc(owner, org, pdf("oc")).json()["id"]
    assert patch(owner, org, did, version=1, notes="первый").status_code == 200
    stale = patch(owner, org, did, version=1, notes="второй")
    assert stale.status_code == 409 and stale.json()["code"] == "document_conflict"
    assert stale.json()["details"][0]["params"] == {"version": 2}
    assert get(owner, org, did)["notes"] == "первый"
    bad = owner.patch(f"{base(org)}/{did}", json={"notes": "x"}, headers=csrf_headers(owner))
    assert bad.status_code == 422  # expected_version обязателен


def test_update_validation(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    cp = new_counterparty(owner, org)
    did = upload_doc(owner, org, pdf("val")).json()["id"]

    def codes(r: Any) -> set[str]:
        return {d["code"] for d in r.json()["details"]}

    assert "number_invalid" in codes(
        patch(owner, org, did, lines=[{"description": "x", "quantity": 1.5}])
    )
    assert "price_invalid" in codes(patch(owner, org, did, total_amount=12.5))
    assert "must_be_non_negative" in codes(patch(owner, org, did, total_amount="-1"))
    assert "too_large" in codes(patch(owner, org, did, total_amount="1" + "0" * 16))
    assert "required" in codes(patch(owner, org, did, lines=[{}]))
    assert "invalid" in codes(patch(owner, org, did, doc_type="selfie"))
    assert patch(owner, org, did, doc_date="not-a-date").status_code == 422
    too_many = patch(owner, org, did, currency="UZS", total_amount="10.005")
    assert too_many.status_code == 422 and "too_many_decimals" in codes(too_many)
    assert "currency_not_enabled" in codes(patch(owner, org, did, currency="USD"))
    assert "currency_unknown" in codes(patch(owner, org, did, currency="XXX"))
    assert "counterparty_unavailable" in codes(
        patch(owner, org, did, counterparty_id="00000000-0000-0000-0000-000000000000")
    )
    assert "unit_unavailable" in codes(
        patch(
            owner,
            org,
            did,
            lines=[{"description": "x", "unit_id": "00000000-0000-0000-0000-000000000000"}],
        )
    )
    assert "product_unavailable" in codes(
        patch(
            owner,
            org,
            did,
            lines=[{"description": "x", "product_id": "00000000-0000-0000-0000-000000000000"}],
        )
    )
    # архивного контрагента выбрать нельзя
    owner.post(
        f"/api/v1/organizations/{org}/counterparties/{cp}/archive", headers=csrf_headers(owner)
    )
    assert "counterparty_unavailable" in codes(patch(owner, org, did, counterparty_id=cp))
    assert get(owner, org, did)["version"] == 1  # ничего из отклонённого не сохранилось


def test_cross_org_references_are_rejected(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    cp_b = new_counterparty(b, org_b)
    unit_b = b.post(
        f"/api/v1/organizations/{org_b}/units",
        json={"code": "box", "name_en": "Box"},
        headers=csrf_headers(b),
    ).json()
    did = upload_doc(a, org_a, pdf("x-org")).json()["id"]
    r = patch(a, org_a, did, counterparty_id=cp_b)
    assert (r.status_code == 422 and r.json()["code"] == "validation_error") or r.json()[
        "code"
    ] == "counterparty_unavailable"
    r2 = patch(a, org_a, did, lines=[{"description": "x", "unit_id": unit_b["id"]}])
    assert r2.status_code == 422 and r2.json()["details"][0]["code"] == "unit_unavailable"


# ---------------- проверки и утверждение ----------------


def issue_codes(d: dict[str, Any]) -> dict[str, str]:
    return {i["code"]: i["severity"] for i in d["issues"]}


def test_issues_for_incomplete_document(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    d = upload_doc(owner, org, pdf("inc")).json()
    assert issue_codes(d) == {
        "date_required": "error",
        "number_required": "error",
        "counterparty_required": "error",
        "total_required": "error",
        "lines_required": "error",
    }
    contract = upload_doc(owner, org, pdf("ct"), doc_type="contract").json()
    assert set(issue_codes(contract)) == {
        "date_required",
        "number_required",
        "counterparty_required",
    }


def test_arithmetic_warnings_and_clean_document(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    cp = new_counterparty(owner, org)
    did = upload_doc(owner, org, pdf("ar")).json()["id"]
    d = patch(owner, org, did, **valid_fields(cp)).json()
    assert d["issues"] == []  # 2 × 100 = 200 = итог
    bad = patch(owner, org, did, total_amount="999").json()
    assert issue_codes(bad) == {"total_mismatch": "warning"}
    bad2 = patch(
        owner,
        org,
        did,
        lines=[{"description": "Т", "quantity": "2", "unit_price": "100", "amount": "250"}],
        total_amount="250",
    ).json()
    li = next(i for i in bad2["issues"] if i["code"] == "line_amount_mismatch")
    assert li["params"] == {"position": 1, "expected": "200.00", "actual": "250.00"}
    # цены с налогом или без — обе схемы итога допустимы
    taxed = patch(
        owner,
        org,
        did,
        lines=[
            {
                "description": "Т",
                "quantity": "2",
                "unit_price": "100",
                "tax_amount": "24",
                "amount": "200",
            }
        ],
        total_amount="224",
        tax_total="24",
    ).json()
    assert taxed["issues"] == []


def test_possible_duplicate_and_counterparty_warnings(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    cp = new_counterparty(owner, org, tin=None)
    first = upload_doc(owner, org, pdf("p1")).json()["id"]
    second = upload_doc(owner, org, pdf("p2")).json()["id"]
    patch(owner, org, first, **valid_fields(cp, number="SAME-1"))
    d = patch(owner, org, second, **valid_fields(cp, number=" same-1 ")).json()
    codes = issue_codes(d)
    assert (
        codes["possible_duplicate"] == "warning" and codes["counterparty_tin_missing"] == "warning"
    )
    dup = next(i for i in d["issues"] if i["code"] == "possible_duplicate")
    assert dup["params"]["document_ids"] == [first]
    # отклонённый документ дубликатом не считается
    act(owner, org, first, "reject", reason="не наш")
    assert "possible_duplicate" not in issue_codes(get(owner, org, second))


def test_approve_flow_with_errors_and_warnings(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    cp = new_counterparty(owner, org)
    did = upload_doc(owner, org, pdf("ap")).json()["id"]
    blocked = act(owner, org, did, "approve")
    assert blocked.status_code == 409 and blocked.json()["code"] == "document_has_errors"
    assert {d["code"] for d in blocked.json()["details"]} >= {"number_required", "lines_required"}

    patch(owner, org, did, **valid_fields(cp, total_amount="999"))  # предупреждение total_mismatch
    needs = act(owner, org, did, "approve")
    assert needs.status_code == 409 and needs.json()["code"] == "warnings_unacknowledged"
    assert [d["code"] for d in needs.json()["details"]] == ["total_mismatch"]
    wrong = act(owner, org, did, "approve", acknowledged=["something_else"])
    assert wrong.status_code == 409

    ok = act(owner, org, did, "approve", acknowledged=["total_mismatch"])
    assert ok.status_code == 200
    d = ok.json()
    assert d["status"] == "APPROVED" and d["approved_by_name"] == "Test User" and d["approved_at"]
    with admin_engine.connect() as c:
        after = c.execute(
            text("SELECT after FROM audit_event WHERE action='document.approved'")
        ).scalar_one()
    assert after["acknowledged"] == ["total_mismatch"]
    # утверждённый документ править нельзя
    ed = patch(owner, org, did, notes="x")
    assert ed.status_code == 409 and ed.json()["code"] == "document_not_editable"
    # повторное утверждение — неверный переход
    assert act(owner, org, did, "approve").json()["code"] == "invalid_status_transition"


def test_approve_blocked_by_unavailable_dependencies(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    did = ready_doc(owner, org, 7)
    cp_id = get(owner, org, did)["counterparty"]["id"]
    owner.post(
        f"/api/v1/organizations/{org}/counterparties/{cp_id}/archive", headers=csrf_headers(owner)
    )
    d = get(owner, org, did)
    assert issue_codes(d)["counterparty_archived"] == "error"
    assert act(owner, org, did, "approve").json()["code"] == "document_has_errors"


def test_status_transitions(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    operator = join(new_client, owner, org, "op@example.uz", "document_operator")
    did = ready_doc(owner, org, 1)

    assert act(operator, org, did, "approve").status_code == 403  # оператор не утверждает
    assert act(owner, org, did, "approve").status_code == 200
    assert (
        act(operator, org, did, "reopen").status_code == 403
    )  # снять утверждение может только утверждающий
    assert act(operator, org, did, "archive").status_code == 403
    re = act(owner, org, did, "reopen")
    assert re.json()["status"] == "NEEDS_REVIEW" and re.json()["approved_by_name"] is None

    assert act(operator, org, did, "reject").status_code == 422  # причина обязательна
    rej = act(operator, org, did, "reject", reason="  Нечитаемый скан ")
    assert (
        rej.status_code == 200
        and rej.json()["status"] == "REJECTED"
        and rej.json()["rejection_reason"] == "Нечитаемый скан"
    )
    assert patch(operator, org, did, notes="x").json()["code"] == "document_not_editable"
    assert (
        act(operator, org, did, "reopen").json()["status"] == "NEEDS_REVIEW"
    )  # из REJECTED вернуть может проверяющий
    assert act(operator, org, did, "reopen").json()["code"] == "invalid_status_transition"

    arch = act(operator, org, did, "archive")
    assert arch.json()["status"] == "ARCHIVED"
    assert act(operator, org, did, "archive").json()["code"] == "invalid_status_transition"
    assert act(operator, org, did, "restore").json()["status"] == "NEEDS_REVIEW"
    acts = actions(admin_engine)
    for a in (
        "document.approved",
        "document.reopened",
        "document.rejected",
        "document.archived",
        "document.restored",
    ):
        assert a in acts, a


def test_mark_duplicate_and_reopen(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    orig = upload_doc(owner, org, pdf("o")).json()["id"]
    copy = upload_doc(owner, org, pdf("c")).json()["id"]
    assert (
        act(owner, org, copy, "mark-duplicate", original_id=copy).status_code == 422
    )  # сам на себя нельзя
    assert (
        act(
            owner, org, copy, "mark-duplicate", original_id="00000000-0000-0000-0000-000000000000"
        ).status_code
        == 422
    )
    d = act(owner, org, copy, "mark-duplicate", original_id=orig)
    assert (
        d.status_code == 200
        and d.json()["status"] == "DUPLICATE"
        and d.json()["duplicate_of_id"] == orig
    )
    third = upload_doc(owner, org, pdf("t")).json()["id"]
    assert (
        act(owner, org, third, "mark-duplicate", original_id=copy).status_code == 422
    )  # оригинал сам дубликат
    back = act(owner, org, copy, "reopen")
    assert back.json()["status"] == "NEEDS_REVIEW" and back.json()["duplicate_of_id"] is None
    # архивирование чистит служебные поля (ограничения БД требуют согласованности)
    act(owner, org, copy, "mark-duplicate", original_id=orig)
    assert act(owner, org, copy, "archive").json()["duplicate_of_id"] is None


def test_stale_version_blocks_actions(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    did = ready_doc(owner, org, 2)
    stale = get(owner, org, did)["version"]
    patch(owner, org, did, notes="правка после того, как проверяющий открыл документ")
    r = act(owner, org, did, "approve", version=stale)
    assert (
        r.status_code == 409 and r.json()["code"] == "document_conflict"
    )  # утверждать устаревшее нельзя
    assert get(owner, org, did)["status"] == "NEEDS_REVIEW"


# ---------------- список ----------------


def test_list_filters_search_sort(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    cp1 = new_counterparty(owner, org, name="ООО Ромашка", tin="111111111")
    cp2 = new_counterparty(owner, org, name="ООО Лютик", tin="222222222")
    d1 = ready_doc(owner, org, 1, cp=cp1, doc_date="2026-09-01")
    d2 = ready_doc(owner, org, 2, cp=cp2, doc_type="act", doc_date="2026-10-05")
    d3 = upload_doc(owner, org, pdf("raw"), name="сканер_001.pdf").json()["id"]
    act(owner, org, d2, "approve")
    act(owner, org, d3, "archive")

    def ids(**q: Any) -> list[str]:
        r = owner.get(base(org), params=q)
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["items"]]

    assert set(ids()) == {d1, d2}  # по умолчанию архивные скрыты
    assert set(ids(status="all")) == {d1, d2, d3}
    assert (
        ids(status="archived") == [d3]
        and ids(status="approved") == [d2]
        and ids(status="needs_review") == [d1]
    )
    assert ids(doc_type="act") == [d2] and ids(counterparty_id=cp1) == [d1]
    assert ids(date_from="2026-10-01") == [d2] and ids(date_to="2026-09-30") == [d1]
    assert (
        ids(q="ромашка") == [d1] and ids(q="N-2") == [d2] and ids(q="сканер", status="all") == [d3]
    )
    assert ids(q="%") == [] and ids(q="нет такого") == []
    newest = ids(status="all")
    assert newest[0] == d3  # по умолчанию новые сверху
    assert ids(status="all", sort="created_at", order="asc")[0] == d1
    assert ids(sort="doc_date", order="desc")[0] == d2
    assert owner.get(base(org), params={"doc_type": "selfie"}).status_code == 422
    assert (
        owner.get(base(org), params={"sort": "total_amount"}).json()["details"][0]["code"]
        == "invalid_sort"
    )
    item = owner.get(base(org), params={"q": "N-2"}).json()["items"][0]
    assert (
        item["counterparty_name"] == "ООО Лютик"
        and item["total_amount"] == "200.00"
        and item["original_name"] == "invoice.pdf"
    )


def test_list_pagination(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    me = owner.get("/api/v1/auth/me").json()["id"]
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO document (id, organization_id, doc_type, status, number, created_by) "
                "SELECT gen_random_uuid(), CAST(:o AS uuid), 'invoice', 'NEEDS_REVIEW', 'N' || i, CAST(:u AS uuid) "
                "FROM generate_series(1, 55) i"
            ),
            {"o": org, "u": me},
        )
    seen: list[str] = []
    for page in (1, 2, 3):
        p = owner.get(base(org), params={"page": page, "page_size": 20}).json()
        assert p["total"] == 55
        seen += [i["id"] for i in p["items"]]
    assert len(seen) == len(set(seen)) == 55


# ---------------- изоляция ----------------


def test_isolation(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    did = ready_doc(a, org_a, 1)
    for path in (
        f"{base(org_b)}/{did}",
        f"{base(org_a)}/{did}",
        f"{base(org_b)}/{did}/file",
        f"{base(org_a)}/{did}/file",
    ):
        assert b.get(path).status_code == 404, path
    assert patch(b, org_b, did, version=1, notes="x").status_code == 404
    assert (
        b.post(
            f"{base(org_b)}/{did}/approve", json={"expected_version": 2}, headers=csrf_headers(b)
        ).status_code
        == 404
    )
    assert b.get(base(org_a)).status_code == 404
    assert upload_doc(b, org_a, pdf("intruder")).status_code == 404
    assert b.get(base(org_b)).json()["total"] == 0
    assert b.get(base(org_b), params={"q": "N-1"}).json()["total"] == 0
    assert a.get(f"{base(org_a)}/{did}").status_code == 200


def test_jpeg_and_png_both_accepted(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    assert upload_doc(owner, org, jpeg_bytes(), name="p.JPG", doc_type="receipt").status_code == 201
    assert (
        upload_doc(
            owner, org, png_bytes(color="blue"), name="p.png", doc_type="receipt"
        ).status_code
        == 201
    )
