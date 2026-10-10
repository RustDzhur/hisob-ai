"""Контекст запроса (correlation id, IP, User-Agent) для логов и аудита."""

from contextvars import ContextVar

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)
user_agent: ContextVar[str | None] = ContextVar("user_agent", default=None)
