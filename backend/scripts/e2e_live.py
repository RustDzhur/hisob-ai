"""Сквозная проверка РЕАЛЬНОГО распознавания на живом стеке (синтетический документ, выдуманные данные).

Запуск на сервере: docker compose run --rm --no-deps -e PYTHONPATH=/srv -e BACKEND_URL=http://backend:8000 backend python scripts/e2e_live.py
"""

import os
import sys
import time
import uuid

import httpx

from scripts.smoke_extraction import synthetic_png

BASE = os.environ.get("BACKEND_URL", "http://backend:8000") + "/api/v1"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60)
    email = f"live-{uuid.uuid4().hex[:8]}@example.uz"
    r = c.post(
        "/auth/register",
        json={"email": email, "password": "correct horse battery", "full_name": "Live Test"},
    )
    assert r.status_code == 201, r.text
    h = {"X-CSRF-Token": c.cookies.get("hisob_csrf") or ""}
    # ИНН «нашей» организации = ИНН покупателя в синтетическом счёте, значит контрагент — продавец
    org = c.post(
        "/organizations",
        json={"name": "Live Org", "tin": "987654321"},
        headers={**h, "Idempotency-Key": uuid.uuid4().hex},
    ).json()["id"]
    cp = c.post(
        f"/organizations/{org}/counterparties",
        json={"name": "Test Supplier LLC", "tin": "123456789"},
        headers=h,
    ).json()["id"]

    st = c.get(f"/organizations/{org}/documents/ai-status").json()
    print("ai-status:", st)
    if not st["configured"]:
        print("AI не настроен у воркера — проверьте контейнер worker и .env")
        return 2

    up = c.post(
        f"/organizations/{org}/documents",
        files={"file": ("synthetic-invoice.png", synthetic_png(), "image/png")},
        data={"doc_type": "other", "extract": "true"},
        headers=h,
    )
    assert up.status_code == 201, up.text
    doc = up.json()
    print(
        "после загрузки:",
        doc["status"],
        "job:",
        doc["extraction"]["job"] and doc["extraction"]["job"]["status"],
    )
    started = time.time()
    while doc["status"] in ("QUEUED", "EXTRACTING") and time.time() - started < 180:
        time.sleep(2)
        doc = c.get(f"/organizations/{org}/documents/{doc['id']}").json()
    job = doc["extraction"]["job"]
    print(
        f"через {time.time() - started:.0f} с: статус={doc['status']} job={job['status']} {job['provider']}/{job['model']} err={job['error_code']}"
    )
    if job["status"] != "succeeded":
        print("message:", job["error_message"])
        return 1
    print(
        f"тип={doc['doc_type']} номер={doc['number']} дата={doc['doc_date']} валюта={doc['currency']} итого={doc['total_amount']} налог={doc['tax_total']}"
    )
    print(
        "контрагент:",
        doc["counterparty"] and (doc["counterparty"]["name"], doc["counterparty"]["id"] == cp),
    )
    for ln in doc["lines"]:
        print(
            f"  строка {ln['position']}: {ln['description']} {ln['quantity']} {ln['unit_code']} x {ln['unit_price']} = {ln['amount']}"
        )
    print("замечания:", [(i["code"], i["severity"]) for i in doc["issues"]])
    fields = {f["path"]: f for f in doc["extraction"]["fields"]}
    print("number:", fields.get("number"))
    print("approved?", doc["status"] == "APPROVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
