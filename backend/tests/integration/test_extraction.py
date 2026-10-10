import io
import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.modules.documents.extraction.providers import MockProvider, ProviderError
from app.modules.documents.extraction.runner import RetryLater
from tests.ai_helpers import good_payload, line, payload, run_job, set_runtime
from tests.files import pdf, png_bytes
from tests.helpers import create_org, csrf_headers, join, register, upload_doc

NewClient = Callable[[], TestClient]
MockFactory = Callable[[Any], MockProvider]
OWN_TIN = "999888777"


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/documents"


def make_org(
    new_client: NewClient, email: str = "owner@example.uz", tin: str | None = OWN_TIN
) -> tuple[TestClient, str]:
    c = new_client()
    register(c, email)
    org = create_org(c, "ООО Наша Компания", **({"tin": tin} if tin else {})).json()["id"]
    return c, org


def post(c: TestClient, path: str, **body: Any) -> Any:
    return c.post(path, json=body, headers=csrf_headers(c))


def get(c: TestClient, org: str, did: str) -> dict[str, Any]:
    r = c.get(f"{base(org)}/{did}")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def start(c: TestClient, org: str, did: str) -> Any:
    return c.post(f"{base(org)}/{did}/extract", headers=csrf_headers(c))


def new_doc(
    c: TestClient, org: str, n: Any = 0, data: bytes | None = None, name: str = "a.pdf"
) -> str:
    r = upload_doc(c, org, data if data is not None else pdf(f"ex-{n}-{org}"), name=name)
    assert r.status_code == 201, r.text
    did: str = r.json()["id"]
    return did


def extract(c: TestClient, org: str, did: str, final: bool = True) -> dict[str, Any]:
    r = start(c, org, did)
    assert r.status_code == 202, r.text
    run_job(r.json()["extraction"]["job"]["id"], org, final)
    return get(c, org, did)


def fields(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {x["path"]: x for x in doc["extraction"]["fields"]}


def actions(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return [
            r[0]
            for r in conn.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


def product(c: TestClient, org: str, sku: str, name: str, barcodes: list[str] | None = None) -> str:
    units = c.get(f"/api/v1/organizations/{org}/units").json()
    uid = next(u["id"] for u in units if u["code"] == "pcs")
    r = c.post(
        f"/api/v1/organizations/{org}/products",
        json={
            "kind": "goods",
            "sku": sku,
            "name": name,
            "unit_id": uid,
            "barcodes": barcodes or [],
        },
        headers=csrf_headers(c),
    )
    assert r.status_code == 201, r.text
    pid: str = r.json()["id"]
    return pid


def counterparty(c: TestClient, org: str, name: str, tin: str | None) -> str:
    r = c.post(
        f"/api/v1/organizations/{org}/counterparties",
        json={"name": name, "tin": tin},
        headers=csrf_headers(c),
    )
    assert r.status_code == 201, r.text
    cid: str = r.json()["id"]
    return cid


# ---------------- запуск ----------------


def test_start_requires_configuration(new_client: NewClient) -> None:
    owner, org = make_org(new_client)
    did = new_doc(owner, org)
    r = start(owner, org, did)
    assert r.status_code == 409 and r.json()["code"] == "ai_not_configured"
    st = owner.get(f"{base(org)}/ai-status").json()
    assert st == {"configured": False, "provider": None, "model": None, "mock": False}
    assert get(owner, org, did)["status"] == "NEEDS_REVIEW"


def test_start_queues_job_and_locks_document(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    assert owner.get(f"{base(org)}/ai-status").json() == {
        "configured": True,
        "provider": "mock",
        "model": "mock-1",
        "mock": True,
    }
    did = new_doc(owner, org)
    v0 = get(owner, org, did)["version"]
    r = start(owner, org, did)
    d = r.json()
    assert r.status_code == 202 and d["status"] == "QUEUED" and d["version"] == v0 + 1
    job = d["extraction"]["job"]
    assert job["status"] == "queued" and job["provider"] == "mock" and job["mock"] is True
    assert "document.extraction_requested" in actions(admin_engine)
    # пока идёт обработка, документ нельзя править, утверждать и запускать второй раз
    edit = owner.patch(
        f"{base(org)}/{did}",
        json={"expected_version": d["version"], "notes": "x"},
        headers=csrf_headers(owner),
    )
    assert edit.status_code == 409 and edit.json()["code"] == "document_not_editable"
    assert (
        post(owner, f"{base(org)}/{did}/approve", expected_version=d["version"]).json()["code"]
        == "invalid_status_transition"
    )
    assert start(owner, org, did).json()["code"] == "invalid_status_transition"
    assert (
        post(owner, f"{base(org)}/{did}/reject", expected_version=d["version"], reason="x").json()[
            "code"
        ]
        == "invalid_status_transition"
    )


def test_concurrent_start_creates_one_job(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    did = new_doc(owner, org)
    from app.main import app

    barrier = threading.Barrier(4)
    out: list[int] = []

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            h = csrf_headers(c)
            barrier.wait()
            out.append(c.post(f"{base(org)}/{did}/extract", headers=h).status_code)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [202, 409, 409, 409], out
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM ai_job")).scalar_one() == 1


def test_enqueue_failure_is_reported_and_rolled_back(
    new_client: NewClient,
    mock_ai: MockFactory,
    monkeypatch: pytest.MonkeyPatch,
    admin_engine: Engine,
) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    did = new_doc(owner, org)
    from app import worker

    def boom(*a: Any, **k: Any) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(worker.extract_document, "send", boom)
    r = start(owner, org, did)
    assert r.status_code == 503 and r.json()["code"] == "queue_unavailable"
    d = get(owner, org, did)
    assert d["status"] == "NEEDS_REVIEW" and d["extraction"]["job"]["status"] == "failed"
    assert d["extraction"]["job"]["error_code"] == "queue_unavailable"
    assert (
        start(owner, org, did).status_code == 503
    )  # после сбоя можно пробовать снова (активной задачи нет)


def test_daily_quota(
    new_client: NewClient, mock_ai: MockFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_max_jobs_per_day", 1)
    extract(owner, org, new_doc(owner, org, 1))
    r = start(owner, org, new_doc(owner, org, 2))
    assert r.status_code == 429 and r.json()["code"] == "ai_quota_exceeded"
    assert r.json()["details"][0]["params"] == {"limit": 1}


def test_upload_with_extract_flag(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)

    def up(n: int) -> Any:
        return owner.post(
            base(org),
            files={"file": (f"{n}.pdf", pdf(f"flag-{n}"), "application/pdf")},
            data={"doc_type": "invoice", "extract": "true"},
            headers=csrf_headers(owner),
        )

    no_ai = up(1)  # AI не настроен: загрузка всё равно удаётся
    assert (
        no_ai.status_code == 201
        and no_ai.json()["status"] == "NEEDS_REVIEW"
        and no_ai.json()["extraction"]["job"] is None
    )
    mock_ai(good_payload())
    with_ai = up(2)
    assert with_ai.status_code == 201 and with_ai.json()["status"] == "QUEUED"
    assert with_ai.json()["extraction"]["job"]["status"] == "queued"


# ---------------- результат ----------------


def test_extraction_fills_empty_fields_with_provenance(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)
    alpha = counterparty(owner, org, "ООО Поставщик Альфа", "123456789")
    milk = product(owner, org, "MILK-1", "Молоко 3% 1л")
    prov = mock_ai(good_payload())
    did = new_doc(owner, org, data=png_bytes(color="green"), name="scan.png")
    v0 = get(owner, org, did)["version"]
    d = extract(owner, org, did)

    assert prov.calls[0].kind == "image" and prov.calls[0].filename == "scan.png"
    assert d["status"] == "NEEDS_REVIEW" and d["version"] > v0 + 1
    assert (d["doc_type"], d["number"], d["doc_date"], d["currency"], d["total_amount"]) == (
        "invoice",
        "INV-100",
        "2026-10-01",
        "UZS",
        "300.00",
    )
    assert (
        d["counterparty"]["id"] == alpha
    )  # ИНН организации = покупатель, значит контрагент — продавец, найден по ИНН
    assert [
        (ln["position"], ln["description"], ln["quantity"], ln["unit_code"], ln["amount"])
        for ln in d["lines"]
    ] == [
        (1, "Молоко 3%", "2", "pcs", "200.00"),
        (2, "Хлеб белый", "10", "kg", "100.00"),
    ]
    assert d["lines"][0]["product_id"] == milk  # артикул = SKU: точное совпадение
    assert d["lines"][1]["product_id"] is None

    ex = d["extraction"]
    assert (
        ex["job"]["status"] == "succeeded"
        and ex["job"]["mock"] is True
        and ex["job"]["error_code"] is None
    )
    fl = fields(d)
    assert fl["number"] == {
        "path": "number",
        "value": "INV-100",
        "confidence": 0.9,
        "evidence": "…INV-100…",
        "validation": "ok",
        "applied": True,
    }
    assert fl["seller_tin"]["applied"] is False and fl["lines.1.quantity"]["applied"] is True
    assert ex["counterparty_candidates"][0] == {
        "id": alpha,
        "name": "ООО Поставщик Альфа",
        "tin": "123456789",
        "reason": "tin_match",
        "score": 1.0,
        "party": "seller",
        "archived": False,
    }
    # подсказки без автоподстановки: проверки считаются уже над заполненным документом
    assert d["issues"] == []

    with admin_engine.connect() as c:
        job = c.execute(
            text("SELECT status, tokens_in, tokens_out, attempts, prompt_version FROM ai_job")
        ).one()
        raw = c.execute(text("SELECT raw_output->'number'->>'value' FROM ai_result")).scalar_one()
        after = c.execute(
            text("SELECT after, actor_user_id FROM audit_event WHERE action='document.extracted'")
        ).one()
    assert tuple(job) == ("succeeded", 100, 50, 1, "v1") and raw == "INV-100"
    assert (
        after[1] is None
        and after[0]["provider"] == "mock"
        and after[0]["lines_created"] == 2
        and after[0]["tokens_in"] == 100
    )
    # AI никогда не утверждает и не проводит
    assert d["status"] != "APPROVED" and d["approved_by_name"] is None


def test_extraction_never_overwrites_manual_work(
    new_client: NewClient, mock_ai: MockFactory
) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    did = new_doc(owner, org)
    cur = get(owner, org, did)
    owner.patch(
        f"{base(org)}/{did}",
        json={
            "expected_version": cur["version"],
            "number": "MY-1",
            "total_amount": "5.00",
            "currency": "UZS",
            "lines": [{"description": "Вручную введённая строка", "amount": "5.00"}],
        },
        headers=csrf_headers(owner),
    )
    d = extract(owner, org, did)
    assert d["number"] == "MY-1" and d["total_amount"] == "5.00"  # руками заполненное не тронуто
    assert [ln["description"] for ln in d["lines"]] == ["Вручную введённая строка"]
    fl = fields(d)
    assert (
        fl["number"]["value"] == "INV-100" and fl["number"]["applied"] is False
    )  # предложено, но не применено
    assert fl["lines.1.description"]["applied"] is False
    assert d["doc_date"] == "2026-10-01" and fl["date"]["applied"] is True  # пустое — заполнено
    # повторное распознавание создаёт новую задачу и новые предложения
    again = extract(owner, org, did)
    assert again["extraction"]["job"]["id"] != d["extraction"]["job"]["id"]


def test_invalid_values_become_suggestions_not_data(
    new_client: NewClient, mock_ai: MockFactory
) -> None:
    owner, org = make_org(new_client)
    bad = payload(
        [line("Товар", "-3", None, "10.00001", "abc", article=None)],
        number="N-1",
        date="вчера",
        currency="USD",
        total_amount="1 250,555",
        tax_total="-4",
    )
    mock_ai(bad)
    d = extract(owner, org, new_doc(owner, org))
    assert d["doc_date"] is None and d["currency"] is None and d["tax_total"] is None
    fl = fields(d)
    assert fl["date"]["validation"] == "invalid" and fl["date"]["applied"] is False
    assert fl["currency"]["validation"] == "invalid"  # USD не включена в организации
    # валюта не определена -> число не проверить на знаки: значение разобрано и применено как есть
    assert fl["total_amount"]["validation"] == "ok" and d["total_amount"] == "1250.555"
    assert fl["tax_total"]["validation"] == "invalid"
    assert (
        fl["lines.1.quantity"]["validation"] == "invalid"
        and fl["lines.1.amount"]["validation"] == "invalid"
    )
    assert fl["lines.1.unit_price"]["validation"] == "invalid"  # больше 4 знаков
    ln = d["lines"][0]
    assert (ln["quantity"], ln["unit_price"], ln["amount"]) == (None, None, None)
    assert d["number"] == "N-1"


def test_money_decimals_respect_document_currency(
    new_client: NewClient, mock_ai: MockFactory
) -> None:
    owner, org = make_org(new_client)
    mock_ai(payload(currency="UZS", total_amount="100.005"))
    d = extract(owner, org, new_doc(owner, org))
    assert (
        d["total_amount"] is None and fields(d)["total_amount"]["validation"] == "invalid"
    )  # UZS: 2 знака, без округления


# ---------------- сопоставление ----------------


def test_counterparty_matching_modes(new_client: NewClient, mock_ai: MockFactory) -> None:
    owner, org = make_org(new_client)
    by_tin = counterparty(owner, org, "Совсем другое название", "123456789")
    similar = counterparty(owner, org, 'ООО "Ромашка Плюс"', None)
    mock_ai(good_payload(seller_name="Ромашка Плюс MCHJ", seller_tin="111222333"))
    d = extract(owner, org, new_doc(owner, org, 1))
    # продавец с неизвестным ИНН: похожее название — только вариант, подстановки нет
    assert d["counterparty"] is None
    cands = d["extraction"]["counterparty_candidates"]
    assert [(c["id"], c["reason"]) for c in cands] == [(similar, "similar_name")] or cands[0][
        "reason"
    ] in ("name_match", "similar_name")
    assert all(c["id"] != by_tin for c in cands)

    # ИНН нашей организации совпал с продавцом -> контрагент — покупатель
    mock_ai(good_payload(seller_tin=OWN_TIN, buyer_tin="123456789", buyer_name="X"))
    d2 = extract(owner, org, new_doc(owner, org, 2))
    assert (
        d2["counterparty"]["id"] == by_tin
        and d2["extraction"]["counterparty_candidates"][0]["party"] == "buyer"
    )


def test_unknown_own_tin_never_auto_links(new_client: NewClient, mock_ai: MockFactory) -> None:
    owner, org = make_org(
        new_client, tin=None
    )  # ИНН нашей организации не задан: сторону определить нельзя
    alpha = counterparty(owner, org, "ООО Поставщик Альфа", "123456789")
    mock_ai(good_payload())
    d = extract(owner, org, new_doc(owner, org))
    assert d["counterparty"] is None
    assert d["extraction"]["counterparty_candidates"][0]["id"] == alpha


def test_archived_counterparty_is_suggested_but_not_linked(
    new_client: NewClient, mock_ai: MockFactory
) -> None:
    owner, org = make_org(new_client)
    cid = counterparty(owner, org, "ООО Поставщик Альфа", "123456789")
    owner.post(
        f"/api/v1/organizations/{org}/counterparties/{cid}/archive", headers=csrf_headers(owner)
    )
    mock_ai(good_payload())
    d = extract(owner, org, new_doc(owner, org))
    assert d["counterparty"] is None
    assert d["extraction"]["counterparty_candidates"][0]["archived"] is True


def test_product_matching_by_barcode_and_name(new_client: NewClient, mock_ai: MockFactory) -> None:
    owner, org = make_org(new_client)
    by_code = product(owner, org, "SKU-A", "Что-то совсем иное", barcodes=["4780001230001"])
    similar = product(owner, org, "SKU-B", "Хлеб белый нарезной")
    mock_ai(
        payload(
            [
                line("Молоко", "1", "шт", "1", "1", article="4780001230001"),
                line("Хлеб белый нарезной", "1", "шт", "1", "1"),
            ],
            number="1",
            date="2026-10-01",
            currency="UZS",
            total_amount="2",
        )
    )
    d = extract(owner, org, new_doc(owner, org))
    assert d["lines"][0]["product_id"] == by_code  # точный штрихкод
    assert d["lines"][1]["product_id"] is None  # по названию — только вариант
    m = d["extraction"]["line_matches"]
    assert m["1"][0]["reason"] == "barcode_match" and m["2"][0] == {
        "id": similar,
        "sku": "SKU-B",
        "name": "Хлеб белый нарезной",
        "reason": "name_match",
        "score": 0.9,
        "archived": False,
    }


# ---------------- входные форматы ----------------


def test_each_file_kind_reaches_the_provider_in_the_right_form(
    new_client: NewClient, mock_ai: MockFactory
) -> None:
    owner, org = make_org(new_client)
    prov = mock_ai(good_payload())
    extract(owner, org, new_doc(owner, org, 1))
    extract(owner, org, new_doc(owner, org, data="номер;сумма\n1;100\n".encode(), name="t.csv"))
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Наименование", "Сумма"])
    ws.append(["Молоко", 200])
    buf = io.BytesIO()
    wb.save(buf)
    xl = new_doc(owner, org, data=buf.getvalue(), name="t.xlsx")
    r = start(owner, org, xl)
    run_job(r.json()["extraction"]["job"]["id"], org)
    assert [c.kind for c in prov.calls] == ["pdf", "text", "text"]
    assert prov.calls[1].text is not None and "DOCUMENT DATA START" in prov.calls[1].text


# ---------------- сбои и устойчивость ----------------


def test_non_retryable_provider_error_fails_job_and_unlocks_document(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)

    def deny(_: Any) -> dict[str, Any]:
        raise ProviderError("provider_auth", "Ключ отклонён провайдером (401).")

    mock_ai(deny)
    did = new_doc(owner, org)
    d = extract(owner, org, did)
    job = d["extraction"]["job"]
    assert (
        job["status"] == "failed"
        and job["error_code"] == "provider_auth"
        and "401" in job["error_message"]
    )
    assert d["status"] == "NEEDS_REVIEW" and d["number"] is None and d["lines"] == []
    assert "document.extraction_failed" in actions(admin_engine)
    # документ снова редактируется и распознавание можно повторить
    assert (
        owner.patch(
            f"{base(org)}/{did}",
            json={"expected_version": d["version"], "notes": "ok"},
            headers=csrf_headers(owner),
        ).status_code
        == 200
    )


def test_retryable_error_retries_then_gives_up(new_client: NewClient, mock_ai: MockFactory) -> None:
    owner, org = make_org(new_client)
    attempts = {"n": 0}

    def flaky(_: Any) -> dict[str, Any]:
        attempts["n"] += 1
        raise ProviderError("provider_rate_limited", "429", retryable=True)

    mock_ai(flaky)
    did = new_doc(owner, org)
    r = start(owner, org, did)
    job_id = r.json()["extraction"]["job"]["id"]
    with pytest.raises(RetryLater):
        run_job(job_id, org, final=False)  # очередь повторит
    d = get(owner, org, did)
    assert d["status"] == "EXTRACTING" and d["extraction"]["job"]["status"] == "running"
    with pytest.raises(RetryLater):
        run_job(job_id, org, final=False)
    run_job(job_id, org, final=True)  # последняя попытка: фиксируем отказ
    d = get(owner, org, did)
    assert d["status"] == "NEEDS_REVIEW" and d["extraction"]["job"]["status"] == "failed"
    assert d["extraction"]["job"]["error_code"] == "provider_rate_limited" and attempts["n"] == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"nothing": "here"},
        {
            **good_payload(),
            "approve_document": True,
        },  # «инструкция» от модели: лишние ключи недопустимы
        {
            **good_payload(),
            "number": {"value": 123, "confidence": 1, "evidence": None},
        },  # число вместо строки
        {k: v for k, v in good_payload().items() if k != "lines"},
    ],
)
def test_invalid_provider_output_is_rejected_not_repaired(
    new_client: NewClient, mock_ai: MockFactory, bad: dict[str, Any]
) -> None:
    owner, org = make_org(new_client)
    mock_ai(bad)
    d = extract(owner, org, new_doc(owner, org))
    assert (
        d["extraction"]["job"]["status"] == "failed"
        and d["extraction"]["job"]["error_code"] == "invalid_provider_output"
    )
    assert d["number"] is None and d["lines"] == [] and d["extraction"]["fields"] == []
    assert d["status"] == "NEEDS_REVIEW"


def test_job_is_idempotent(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)
    prov = mock_ai(good_payload())
    did = new_doc(owner, org)
    r = start(owner, org, did)
    job_id = r.json()["extraction"]["job"]["id"]
    run_job(job_id, org)
    run_job(job_id, org)  # повторная доставка сообщения очереди
    assert len(prov.calls) == 1
    d = get(owner, org, did)
    assert len(d["lines"]) == 2 and len(d["extraction"]["fields"]) == len(
        {x["path"] for x in d["extraction"]["fields"]}
    )
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM audit_event WHERE action='document.extracted'")
            ).scalar_one()
            == 1
        )
    run_job(str(uuid.uuid4()), org)  # несуществующая задача — тихо ничего не делает


def test_cancel_while_queued_and_while_running(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)
    prov = mock_ai(good_payload())
    did = new_doc(owner, org, 1)
    job_id = start(owner, org, did).json()["extraction"]["job"]["id"]
    c = post(owner, f"{base(org)}/{did}/extract/cancel")
    assert (
        c.status_code == 200
        and c.json()["status"] == "NEEDS_REVIEW"
        and c.json()["extraction"]["job"]["status"] == "cancelled"
    )
    run_job(job_id, org)  # воркер получил сообщение уже после отмены
    assert prov.calls == [] and get(owner, org, did)["number"] is None
    assert (
        post(owner, f"{base(org)}/{did}/extract/cancel").json()["code"]
        == "invalid_status_transition"
    )

    # отмена в момент запроса к провайдеру: результат отбрасывается
    did2 = new_doc(owner, org, 2)
    job2 = start(owner, org, did2).json()["extraction"]["job"]["id"]

    def cancel_during_call(inp: Any) -> dict[str, Any]:
        assert post(owner, f"{base(org)}/{did2}/extract/cancel").status_code == 200
        return good_payload()

    mock_ai(cancel_during_call)
    run_job(job2, org)
    d = get(owner, org, did2)
    assert (
        d["extraction"]["job"]["status"] == "cancelled" and d["number"] is None and d["lines"] == []
    )
    assert "document.extraction_cancelled" in actions(admin_engine)


# ---------------- права и изоляция ----------------

CAN_START = {
    "owner": True,
    "org_admin": True,
    "accountant": True,
    "document_operator": True,
    "sales_purchase_manager": True,
    "storekeeper": False,
    "auditor": False,
}


@pytest.mark.parametrize("role", list(CAN_START))
def test_permissions(new_client: NewClient, mock_ai: MockFactory, role: str) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    did = new_doc(owner, org)
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    assert (
        actor.get(f"{base(org)}/ai-status").status_code == 200
    )  # статус видят все, кто читает документы
    r = start(actor, org, did)
    assert r.status_code == (202 if CAN_START[role] else 403)
    if CAN_START[role]:
        assert post(actor, f"{base(org)}/{did}/extract/cancel").status_code == 200
    else:
        assert post(actor, f"{base(org)}/{did}/extract/cancel").status_code == 403


def test_isolation(new_client: NewClient, mock_ai: MockFactory) -> None:
    a, org_a = make_org(new_client, "a@example.uz")
    b, org_b = make_org(new_client, "b@example.uz", tin="111111111")
    mock_ai(good_payload())
    did = new_doc(a, org_a)
    assert start(b, org_b, did).status_code == 404
    assert start(b, org_a, did).status_code == 404
    assert post(b, f"{base(org_b)}/{did}/extract/cancel").status_code == 404
    assert b.get(f"{base(org_a)}/ai-status").status_code == 404
    d = extract(a, org_a, did)
    assert d["extraction"]["job"]["status"] == "succeeded"
    # у другой организации нет ни задач, ни полей
    other = new_doc(b, org_b)
    assert get(b, org_b, other)["extraction"] == {
        "job": None,
        "fields": [],
        "counterparty_candidates": [],
        "line_matches": {},
    }


def test_provider_changed_after_enqueue_is_refused(
    new_client: NewClient, mock_ai: MockFactory, admin_engine: Engine
) -> None:
    owner, org = make_org(new_client)
    mock_ai(good_payload())
    did = new_doc(owner, org)
    job_id = start(owner, org, did).json()["extraction"]["job"]["id"]
    from app.modules.documents.extraction.providers import set_provider_for_tests

    other = MockProvider(good_payload())
    other.name = "openai"
    set_provider_for_tests(other)
    run_job(job_id, org)
    d = get(owner, org, did)
    assert d["extraction"]["job"]["error_code"] == "provider_changed" and other.calls == []
    set_runtime(admin_engine)
