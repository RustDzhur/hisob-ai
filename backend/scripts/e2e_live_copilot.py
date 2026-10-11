"""Сквозная проверка РЕАЛЬНОГО Copilot на живом стеке (выдуманные данные, отдельная организация).

Запуск на сервере: docker compose run --rm --no-deps -e PYTHONPATH=/srv -e BACKEND_URL=http://backend:8000 backend python scripts/e2e_live_copilot.py
"""

import os
import sys
import time
import uuid

import httpx

BASE = os.environ.get("BACKEND_URL", "http://backend:8000") + "/api/v1"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60)
    email = f"live-{uuid.uuid4().hex[:8]}@example.uz"
    assert (
        c.post(
            "/auth/register",
            json={"email": email, "password": "correct horse battery", "full_name": "Live Test"},
        ).status_code
        == 201
    )
    h = {"X-CSRF-Token": c.cookies.get("hisob_csrf") or ""}
    org = c.post(
        "/organizations",
        json={"name": "Live Org"},
        headers={**h, "Idempotency-Key": uuid.uuid4().hex},
    ).json()["id"]
    cp = c.post(
        f"/organizations/{org}/counterparties",
        json={"name": "Test Supplier LLC", "tin": "123456789"},
        headers=h,
    ).json()["id"]
    pdf = f"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n%{uuid.uuid4().hex}\n".encode()
    doc = c.post(
        f"/organizations/{org}/documents",
        files={"file": ("inv.pdf", pdf, "application/pdf")},
        data={"doc_type": "invoice"},
        headers=h,
    ).json()
    body = {
        "expected_version": doc["version"],
        "doc_type": "invoice",
        "number": "SF-LIVE-1",
        "doc_date": "2026-09-01",
        "counterparty_id": cp,
        "currency": "UZS",
        "total_amount": "1000",
        "lines": [
            {"description": "Service", "quantity": "1", "unit_price": "1000", "amount": "1000"}
        ],
    }
    p = c.patch(f"/organizations/{org}/documents/{doc['id']}", json=body, headers=h).json()
    ok = c.post(
        f"/organizations/{org}/documents/{doc['id']}/approve",
        json={"expected_version": p["version"], "acknowledged": []},
        headers=h,
    )
    assert ok.status_code == 200, ok.text

    st = c.get(f"/organizations/{org}/copilot/status").json()
    print("copilot status:", st)
    if not st["configured"]:
        print("AI не настроен у воркера")
        return 2
    r = c.post(
        f"/organizations/{org}/copilot/ask",
        json={
            "question": "Which of our documents are unpaid and how much is left on each?",
            "locale": "en",
        },
        headers=h,
    )
    assert r.status_code == 202, r.text
    cid = r.json()["conversation_id"]
    started = time.time()
    msg = r.json()["message"]
    while msg["status"] in ("PENDING", "RUNNING") and time.time() - started < 120:
        time.sleep(2)
        msg = c.get(f"/organizations/{org}/copilot/conversations/{cid}").json()["messages"][-1]
    print(
        f"через {time.time() - started:.0f} с: статус={msg['status']} {msg['provider']}/{msg['model']} err={msg['error_code']}"
    )
    print("tools:", [(t["tool"], t.get("rows"), t.get("error")) for t in msg["tool_trace"]])
    print("answer:", msg["content"][:600])
    good = (
        msg["status"] == "DONE"
        and any(t["tool"] == "unpaid_documents" for t in msg["tool_trace"])
        and "SF-LIVE-1" in msg["content"]
    )
    print("OK" if good else "CHECK MANUALLY")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
