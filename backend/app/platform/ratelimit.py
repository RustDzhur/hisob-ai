"""Ограничение частоты запросов (фиксированное окно). Redis, если задан REDIS_URL, иначе память процесса.

Ключ — хэш cookie сессии (вошедший пользователь) либо IP клиента. При недоступности Redis — пропускаем запросы
(доступность важнее) и пишем предупреждение. Ограничения входа по email (5 неудач за 15 минут) работают отдельно.
"""

import hashlib
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.platform import context
from app.platform.config import get_settings
from app.platform.errors import error_body

log = logging.getLogger("app.ratelimit")
SESSION_COOKIE = "hisob_session"


@dataclass(frozen=True)
class Rule:
    name: str
    method: str  # "*" — любой
    pattern: re.Pattern[str]
    limit: int
    window: int  # секунд
    by: str  # "ip" | "actor"


def _r(name: str, method: str, pattern: str, limit: int, window: int, by: str = "actor") -> Rule:
    return Rule(name, method, re.compile(pattern), limit, window, by)


# Порядок важен: действуют все подходящие правила. Числа — стартовые, подбираются по нагрузке.
RULES: list[Rule] = [
    _r("register", "POST", r"^/api/v1/auth/register$", 10, 3600, "ip"),
    _r("login", "POST", r"^/api/v1/auth/login$", 30, 600, "ip"),
    _r("invitation", "*", r"^/api/v1/invitations/", 60, 600, "ip"),
    _r(
        "upload",
        "POST",
        r"^/api/v1/organizations/[^/]+/(documents|bank/accounts/[^/]+/(imports|preview|headers))$",
        60,
        600,
    ),
    _r("export", "GET", r"\.csv$", 30, 600),
    _r("general", "*", r"^/api/", 1200, 60),
]


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, window: int) -> int:
        now = time.time()
        with self._lock:
            started, count = self._data.get(key, (now, 0))
            if now - started >= window:
                started, count = now, 0
            count += 1
            self._data[key] = (started, count)
            if len(self._data) > 50_000:  # защита памяти: выбрасываем истёкшие
                self._data = {k: v for k, v in self._data.items() if now - v[0] < 3600}
            return count

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, socket_timeout=0.3, socket_connect_timeout=0.3)

    def hit(self, key: str, window: int) -> int:
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        count = pipe.execute()[0]
        return int(count)


_store: Any = None


def get_store() -> Any:
    global _store
    if _store is None:
        url = get_settings().redis_url
        _store = RedisStore(url) if url else MemoryStore()
    return _store


def set_store_for_tests(store: Any) -> None:
    global _store
    _store = store


def _actor(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return "s:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "i:" + (context.client_ip.get() or "unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not get_settings().rate_limit_enabled or not path.startswith("/api/"):
            return await call_next(request)
        ip = "i:" + (context.client_ip.get() or "unknown")
        actor = _actor(request)
        store = get_store()
        for rule in RULES:
            if rule.method not in ("*", request.method) or not rule.pattern.search(path):
                continue
            who = ip if rule.by == "ip" else actor
            bucket = int(time.time() // rule.window)
            key = f"rl:{rule.name}:{who}:{bucket}"
            try:
                count = await run_in_threadpool(store.hit, key, rule.window)
            except Exception:
                log.warning("rate limit store unavailable, request allowed")
                break
            if count > rule.limit:
                retry = rule.window - int(time.time() % rule.window)
                log.warning("rate limited rule=%s", rule.name)
                return JSONResponse(
                    error_body("rate_limited", "Слишком много запросов. Повторите позже."),
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)
