"""Нагрузочная проверка чтения (ориентир ТЗ §14: API read p95 < 500 мс) на ВЫДУМАННЫХ данных отдельной организации.

Запуск на сервере (ограничение частоты на стенде выключено):
  docker compose run --rm --no-deps -e PYTHONPATH=/srv -e BACKEND_URL=http://backend:8000 backend \
      python scripts/loadtest.py [потоков=20] [секунд=20] [контрагентов=300]
Цифры зависят от железа и диска сервера; это ориентир, а не гарантия.
"""

import os
import statistics
import sys
import threading
import time
import uuid

import httpx

BASE = os.environ.get("BACKEND_URL", "http://backend:8000") + "/api/v1"
THREADS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
N_CP = int(sys.argv[3]) if len(sys.argv) > 3 else 300


def seed() -> tuple[str, dict[str, str]]:
    c = httpx.Client(base_url=BASE, timeout=60)
    email = f"load-{uuid.uuid4().hex[:8]}@example.uz"
    assert (
        c.post(
            "/auth/register",
            json={"email": email, "password": "correct horse battery", "full_name": "Load Test"},
        ).status_code
        == 201
    )
    h = {"X-CSRF-Token": c.cookies.get("hisob_csrf") or ""}
    org = c.post(
        "/organizations",
        json={"name": "Load Org"},
        headers={**h, "Idempotency-Key": uuid.uuid4().hex},
    ).json()["id"]
    for i in range(N_CP):
        r = c.post(
            f"/organizations/{org}/counterparties",
            json={"name": f"Контрагент {i:04d}", "tin": str(500000000 + i)},
            headers=h,
        )
        assert r.status_code == 201, r.text
    cookies = {k: v for k, v in c.cookies.items()}
    return org, cookies


def main() -> int:
    t0 = time.time()
    org, cookies = seed()
    print(f"подготовка: организация с {N_CP} контрагентами за {time.time() - t0:.0f} с")
    o = f"/organizations/{org}"
    endpoints = [
        f"{o}/counterparties?page_size=25&q=000",
        f"{o}/counterparties?page=3&page_size=25&sort=name",
        f"{o}/products?page_size=25",
        f"{o}/documents?page_size=25",
        f"{o}/stock/balances?page_size=25",
        f"{o}/bank/transactions?page_size=25",
        f"{o}/accounting/entries?page_size=25",
        f"{o}/integrations",
        "/auth/me",
    ]
    lat: dict[str, list[float]] = {e.split("?")[0]: [] for e in endpoints}
    errors: dict[str, int] = {}
    lock = threading.Lock()
    stop = time.time() + SECONDS

    def worker(n: int) -> None:
        c = httpx.Client(base_url=BASE, timeout=30, cookies=cookies)
        i = n
        while time.time() < stop:
            e = endpoints[i % len(endpoints)]
            i += 1
            s = time.perf_counter()
            try:
                r = c.get(e)
                ok = r.status_code == 200
            except httpx.HTTPError:
                ok = False
            d = time.perf_counter() - s
            key = e.split("?")[0]
            with lock:
                lat[key].append(d)
                if not ok:
                    errors[key] = errors.get(key, 0) + 1

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(THREADS)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - started

    def pct(v: list[float], p: float) -> float:
        v = sorted(v)
        return v[min(len(v) - 1, int(len(v) * p))] * 1000

    total = sum(len(v) for v in lat.values())
    print(
        f"{THREADS} потоков, {elapsed:.0f} с, запросов: {total}, {total / elapsed:.1f} запросов/с, ошибок: {sum(errors.values())}"
    )
    print(f"{'эндпоинт':50} {'n':>5} {'p50':>7} {'p95':>7} {'p99':>7}  (мс)")
    worst = 0.0
    for k, v in lat.items():
        if not v:
            continue
        p95 = pct(v, 0.95)
        worst = max(worst, p95)
        print(
            f"{k.replace(o, '{org}'):50} {len(v):5d} {statistics.median(v) * 1000:7.0f} {p95:7.0f} {pct(v, 0.99):7.0f}"
        )
    print(
        f"худший p95: {worst:.0f} мс; ориентир ТЗ < 500 мс: {'выполнен' if worst < 500 else 'НЕ выполнен'}"
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
