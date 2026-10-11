"""Метрики в формате Prometheus (текст). Без внешних библиотек; значения — в памяти процесса.

Метки — только метод, шаблон маршрута (не путь с идентификаторами) и класс статуса: кардинальность ограничена.
"""

import threading

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_lock = threading.Lock()
_count: dict[tuple[str, str, str], int] = {}
_hist: dict[tuple[str, str], list[int]] = {}
_sum: dict[tuple[str, str], float] = {}


def observe(method: str, route: str, status: int, seconds: float) -> None:
    cls = f"{status // 100}xx"
    key = (method, route)
    with _lock:
        _count[(method, route, cls)] = _count.get((method, route, cls), 0) + 1
        buckets = _hist.setdefault(key, [0] * (len(BUCKETS) + 1))
        for i, b in enumerate(BUCKETS):
            if seconds <= b:
                buckets[i] += 1
        buckets[-1] += 1  # +Inf = всего
        _sum[key] = _sum.get(key, 0.0) + seconds


def reset() -> None:
    with _lock:
        _count.clear()
        _hist.clear()
        _sum.clear()


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render() -> str:
    lines = [
        "# HELP hisob_http_requests_total Запросов по методу, маршруту и классу статуса.",
        "# TYPE hisob_http_requests_total counter",
    ]
    with _lock:
        for (m, r, c), n in sorted(_count.items()):
            lines.append(
                f'hisob_http_requests_total{{method="{m}",route="{_esc(r)}",status="{c}"}} {n}'
            )
        lines += [
            "# HELP hisob_http_request_duration_seconds Длительность запросов.",
            "# TYPE hisob_http_request_duration_seconds histogram",
        ]
        for (m, r), buckets in sorted(_hist.items()):
            lab = f'method="{m}",route="{_esc(r)}"'
            for i, b in enumerate(BUCKETS):
                lines.append(
                    f'hisob_http_request_duration_seconds_bucket{{{lab},le="{b}"}} {buckets[i]}'
                )
            lines.append(
                f'hisob_http_request_duration_seconds_bucket{{{lab},le="+Inf"}} {buckets[-1]}'
            )
            lines.append(f"hisob_http_request_duration_seconds_sum{{{lab}}} {_sum[(m, r)]:.6f}")
            lines.append(f"hisob_http_request_duration_seconds_count{{{lab}}} {buckets[-1]}")
    return "\n".join(lines) + "\n"
