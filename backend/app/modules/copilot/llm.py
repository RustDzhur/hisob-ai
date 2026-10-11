"""Чат-адаптеры провайдеров с вызовом инструментов. Ключи — только у воркера.

Внутренний формат истории не зависит от провайдера:
  {"type": "user"|"assistant", "text"}, {"type": "tool_call", "id", "name", "args"}, {"type": "tool_result", "id", "output"}.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.modules.documents.extraction.providers import ProviderError, _post
from app.platform.config import get_settings


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: str  # JSON-строка как её прислал провайдер


@dataclass(frozen=True)
class Turn:
    text: str | None
    calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int | None = None
    tokens_out: int | None = None
    raw_items: list[dict[str, Any]] = field(
        default_factory=list
    )  # служебные элементы провайдера для возврата в историю


class ChatProvider(Protocol):
    name: str
    model: str
    mock: bool

    def configured(self) -> bool: ...
    def step(
        self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Turn: ...


def _tool_def_openai(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": t["name"],
        "description": t["description"],
        "parameters": t["parameters"],
    }


class OpenAIChat:
    """OpenAI Responses API с function calling. store=false: историю целиком передаём сами."""

    name = "openai"
    mock = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._key, self.model, self._base = api_key, model, base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def configured(self) -> bool:
        return bool(self._key and self.model)

    def build_body(
        self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for h in history:
            t = h["type"]
            if t == "user":
                items.append({"role": "user", "content": h["text"]})
            elif t == "assistant":
                items.append({"role": "assistant", "content": h["text"]})
            elif t == "provider_items":
                items.extend(h["items"])
            elif t == "tool_result":
                items.append(
                    {"type": "function_call_output", "call_id": h["id"], "output": h["output"]}
                )
        return {
            "model": self.model,
            "instructions": system,
            "input": items,
            "tools": [_tool_def_openai(t) for t in tools],
            "max_output_tokens": 4000,
            "store": False,
        }

    def step(self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Turn:
        if not self.configured():
            raise ProviderError("provider_not_configured", "Ключ или модель OpenAI не заданы.")
        data = _post(
            self._client,
            f"{self._base}/v1/responses",
            {"Authorization": f"Bearer {self._key}"},
            self.build_body(system, history, tools),
        )
        if data.get("status") not in (None, "completed"):
            raise ProviderError("provider_incomplete", f"Ответ не завершён ({data.get('status')}).")
        texts: list[str] = []
        calls: list[ToolCall] = []
        raw: list[dict[str, Any]] = []
        for item in data.get("output") or []:
            kind = item.get("type")
            if kind == "function_call":
                calls.append(
                    ToolCall(
                        str(item.get("call_id")),
                        str(item.get("name")),
                        str(item.get("arguments") or "{}"),
                    )
                )
                raw.append(item)
            elif kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "refusal":
                        raise ProviderError("provider_refusal", "Провайдер отказался отвечать.")
                    if part.get("type") == "output_text" and part.get("text"):
                        texts.append(part["text"])
            elif kind == "reasoning":
                raw.append(item)
        usage = data.get("usage") or {}
        return Turn(
            "\n".join(texts) or None,
            calls,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            raw,
        )


class DeepSeekChat:
    """DeepSeek chat completions (OpenAI-совместимый) с tools."""

    name = "deepseek"
    mock = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._key, self.model, self._base = api_key, model, base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def configured(self) -> bool:
        return bool(self._key and self.model)

    def build_body(
        self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
        pending: list[dict[str, Any]] = []
        for h in history:
            t = h["type"]
            if t in ("user", "assistant"):
                msgs.append({"role": t, "content": h["text"]})
            elif t == "tool_call":
                pending.append(
                    {
                        "id": h["id"],
                        "type": "function",
                        "function": {"name": h["name"], "arguments": h["args"]},
                    }
                )
            elif t == "tool_result":
                if pending:
                    msgs.append({"role": "assistant", "content": None, "tool_calls": pending})
                    pending = []
                msgs.append({"role": "tool", "tool_call_id": h["id"], "content": h["output"]})
        return {
            "model": self.model,
            "messages": msgs,
            "tools": [{"type": "function", "function": t} for t in tools],
            "max_tokens": 4000,
            "stream": False,
        }

    def step(self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Turn:
        if not self.configured():
            raise ProviderError("provider_not_configured", "Ключ или модель DeepSeek не заданы.")
        data = _post(
            self._client,
            f"{self._base}/chat/completions",
            {"Authorization": f"Bearer {self._key}"},
            self.build_body(system, history, tools),
        )
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("provider_empty_output", "Пустой ответ провайдера.", retryable=True)
        ch = choices[0]
        if ch.get("finish_reason") == "length":
            raise ProviderError("provider_incomplete", "Ответ обрезан по длине.")
        msg = ch.get("message") or {}
        calls = [
            ToolCall(
                str(c.get("id")),
                str((c.get("function") or {}).get("name")),
                str((c.get("function") or {}).get("arguments") or "{}"),
            )
            for c in msg.get("tool_calls") or []
        ]
        usage = data.get("usage") or {}
        return Turn(
            msg.get("content") or None,
            calls,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )


class MockChat:
    """Только для тестов: сценарий из функции (история -> Turn). Всегда помечается как mock."""

    name = "mock"
    model = "mock"
    mock = True

    def __init__(self, script: Any) -> None:
        self._script = script

    def configured(self) -> bool:
        return True

    def step(self, system: str, history: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Turn:
        out: Turn = self._script(history, tools)
        return out


_override: ChatProvider | None = None


def set_chat_provider_for_tests(p: ChatProvider | None) -> None:
    global _override
    _override = p


def _build(name: str) -> ChatProvider | None:
    s = get_settings()
    if name == "openai":
        return OpenAIChat(
            api_key=s.openai_api_key,
            model=s.openai_model,
            base_url=s.openai_base_url,
            timeout=s.ai_timeout_seconds,
        )
    if name == "deepseek":
        return DeepSeekChat(
            api_key=s.deepseek_api_key,
            model=s.deepseek_model,
            base_url=s.deepseek_base_url,
            timeout=s.ai_timeout_seconds,
        )
    return None


def get_chat_provider() -> ChatProvider:
    """Основной провайдер; если он не настроен, а запасной настроен — запасной."""
    if _override is not None:
        return _override
    s = get_settings()
    primary = _build(s.ai_provider)
    if primary is not None and primary.configured():
        return primary
    fb = get_fallback_chat(primary)
    if fb is not None:
        return fb
    if primary is None:
        raise ProviderError("provider_not_configured", f"Неизвестный провайдер: {s.ai_provider!r}.")
    return primary


def get_fallback_chat(primary: ChatProvider | None) -> ChatProvider | None:
    """Запасной провайдер: настроен и отличается от основного; используется только при отказе основного."""
    if _override is not None:
        return None
    s = get_settings()
    name = s.ai_fallback_provider.strip().lower()
    if not name or name == s.ai_provider:
        return None
    fb = _build(name)
    if fb is None or not fb.configured() or (primary is not None and primary.name == fb.name):
        return None
    return fb


def json_dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)
