"""Адаптеры провайдеров распознавания. Единый интерфейс; ключи — только у воркера.

Форматы запросов сверены с официальной документацией (OpenAI Responses API; DeepSeek chat completions).
Mock-провайдер существует только для тестов и всегда помечается в интерфейсе.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.modules.documents.extraction import schema
from app.modules.documents.extraction.inputs import ProviderInput
from app.platform.config import get_settings

_REQUEST_TEXT = "Extract the data from this document and return it as JSON that matches the schema."


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:300]  # без тел ответов целиком и без заголовков
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderResult:
    payload: dict[str, Any]
    tokens_in: int | None
    tokens_out: int | None
    request_id: str | None = None


class ExtractionProvider(Protocol):
    name: str
    model: str
    mock: bool

    def configured(self) -> bool: ...
    def extract(self, inp: ProviderInput) -> ProviderResult: ...


def _error_from_status(status: int, body: str) -> ProviderError:
    detail = ""
    try:
        err = json.loads(body).get("error")
        detail = (err.get("message") if isinstance(err, dict) else str(err)) or ""
    except (ValueError, AttributeError):
        pass
    detail = detail[:200]
    if status in (401, 403):
        return ProviderError("provider_auth", f"Ключ отклонён провайдером ({status}).")
    if status == 404:
        return ProviderError("provider_model_unavailable", f"Модель или адрес недоступны: {detail}")
    if status in (408, 409, 429) or status >= 500:
        code = "provider_rate_limited" if status == 429 else "provider_unavailable"
        return ProviderError(code, f"Временная ошибка провайдера ({status}).", retryable=True)
    return ProviderError(
        "provider_bad_request", f"Запрос отклонён провайдером ({status}): {detail}"
    )


def _post(
    client: httpx.Client, url: str, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    try:
        resp = client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout", "Провайдер не ответил вовремя.", retryable=True
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "provider_unavailable", "Не удалось связаться с провайдером.", retryable=True
        ) from exc
    if resp.status_code >= 400:
        raise _error_from_status(resp.status_code, resp.text)
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_invalid_response", "Ответ провайдера не является JSON."
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError("provider_invalid_response", "Неожиданный формат ответа провайдера.")
    return data


def _json_object(text: str) -> dict[str, Any]:
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise ProviderError("invalid_provider_output", "Провайдер вернул не JSON.") from exc
    if not isinstance(obj, dict):
        raise ProviderError("invalid_provider_output", "Провайдер вернул JSON не того вида.")
    return obj


class OpenAIProvider:
    """OpenAI Responses API: изображения и PDF как base64, ответ по строгой JSON-схеме (Structured Outputs)."""

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

    def build_body(self, inp: ProviderInput) -> dict[str, Any]:
        if inp.kind == "image":
            content: list[dict[str, Any]] = [
                {"type": "input_text", "text": _REQUEST_TEXT},
                {"type": "input_image", "image_url": inp.data_url(), "detail": "high"},
            ]
        elif inp.kind == "pdf":
            content = [
                {"type": "input_file", "filename": inp.filename, "file_data": inp.data_url()},
                {"type": "input_text", "text": _REQUEST_TEXT},
            ]
        else:
            content = [{"type": "input_text", "text": f"{_REQUEST_TEXT}\n\n{inp.text}"}]
        return {
            "model": self.model,
            "instructions": schema.INSTRUCTIONS,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema.SCHEMA_NAME,
                    "schema": schema.json_schema(),
                    "strict": True,
                }
            },
            "max_output_tokens": 16000,
            "store": False,  # не просим провайдера хранить запрос и ответ
        }

    def extract(self, inp: ProviderInput) -> ProviderResult:
        if not self.configured():
            raise ProviderError("provider_not_configured", "Ключ или модель OpenAI не заданы.")
        data = _post(
            self._client,
            f"{self._base}/v1/responses",
            {"Authorization": f"Bearer {self._key}"},
            self.build_body(inp),
        )
        if data.get("status") not in (None, "completed"):
            raise ProviderError(
                "provider_incomplete", f"Ответ не завершён ({data.get('status')}).", retryable=False
            )
        text: str | None = None
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise ProviderError(
                        "provider_refusal", "Провайдер отказался обрабатывать документ."
                    )
                if part.get("type") == "output_text":
                    text = part.get("text")
        if not text:
            raise ProviderError("provider_empty_output", "Пустой ответ провайдера.", retryable=True)
        usage = data.get("usage") or {}
        return ProviderResult(
            _json_object(text),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            data.get("id"),
        )


class DeepSeekProvider:
    """DeepSeek chat completions (OpenAI-совместимый): изображения как base64, JSON Output без строгой схемы."""

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

    def build_body(self, inp: ProviderInput) -> dict[str, Any]:
        if inp.kind == "pdf":
            # в документации DeepSeek PDF как вход не описан: не отправляем и не притворяемся
            raise ProviderError(
                "provider_unsupported_input", "DeepSeek: PDF напрямую не поддерживается."
            )
        shape = json.dumps(_example_shape(), ensure_ascii=False)
        system = f"{schema.INSTRUCTIONS}\nRespond with a single json object. Example json shape:\n{shape}"
        if inp.kind == "image":
            user: list[dict[str, Any]] = [
                {"type": "text", "text": _REQUEST_TEXT},
                {"type": "image_url", "image_url": {"url": inp.data_url(), "detail": "high"}},
            ]
        else:
            user = [{"type": "text", "text": f"{_REQUEST_TEXT}\n\n{inp.text}"}]
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "max_tokens": 8000,
            "stream": False,
        }

    def extract(self, inp: ProviderInput) -> ProviderResult:
        if not self.configured():
            raise ProviderError("provider_not_configured", "Ключ или модель DeepSeek не заданы.")
        data = _post(
            self._client,
            f"{self._base}/chat/completions",
            {"Authorization": f"Bearer {self._key}"},
            self.build_body(inp),
        )
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("provider_empty_output", "Пустой ответ провайдера.", retryable=True)
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ProviderError("provider_incomplete", "Ответ обрезан по длине.")
        content = (choice.get("message") or {}).get("content")
        if not content:  # документация предупреждает: изредка приходит пустой ответ
            raise ProviderError("provider_empty_output", "Пустой ответ провайдера.", retryable=True)
        usage = data.get("usage") or {}
        return ProviderResult(
            _json_object(content),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            data.get("id"),
        )


def _example_shape() -> dict[str, Any]:
    f = {"value": None, "confidence": None, "evidence": None}
    shape: dict[str, Any] = {name: dict(f) for name in schema.HEADER_FIELDS}
    shape["lines"] = [{name: dict(f) for name in schema.LINE_FIELDS}]
    return shape


class MockProvider:
    """ТОЛЬКО для тестов и демонстрации контракта. Результаты не являются распознаванием."""

    name = "mock"
    mock = True

    def __init__(
        self,
        handler: Callable[[ProviderInput], dict[str, Any]] | dict[str, Any],
        model: str = "mock-1",
    ) -> None:
        self.model = model
        self._handler = handler
        self.calls: list[ProviderInput] = []

    def configured(self) -> bool:
        return True

    def extract(self, inp: ProviderInput) -> ProviderResult:
        self.calls.append(inp)
        payload = self._handler(inp) if callable(self._handler) else self._handler
        return ProviderResult(payload, 100, 50, "mock-request")


_override: ExtractionProvider | None = None
_fallback_override: ExtractionProvider | None = None
_NO_FALLBACK = object()


def set_provider_for_tests(
    provider: ExtractionProvider | None, fallback: ExtractionProvider | None = None
) -> None:
    global _override, _fallback_override
    _override = provider
    _fallback_override = fallback


def _build(name: str) -> ExtractionProvider:
    s = get_settings()
    if name == "openai":
        return OpenAIProvider(
            api_key=s.openai_api_key,
            model=s.openai_model,
            base_url=s.openai_base_url,
            timeout=s.ai_timeout_seconds,
        )
    if name == "deepseek":
        return DeepSeekProvider(
            api_key=s.deepseek_api_key,
            model=s.deepseek_model,
            base_url=s.deepseek_base_url,
            timeout=s.ai_timeout_seconds,
        )
    raise ProviderError("provider_not_configured", f"Неизвестный провайдер: {name!r}.")


def get_provider() -> ExtractionProvider:
    """Основной провайдер. Если он не настроен, а запасной настроен — работает запасной."""
    if _override is not None:
        return _override
    s = get_settings()
    primary = _build(s.ai_provider)
    if not primary.configured():
        fb = get_fallback_provider(primary)
        if fb is not None:
            return fb
    return primary


def get_fallback_provider(primary: ExtractionProvider | None = None) -> ExtractionProvider | None:
    """Запасной провайдер: настроен и отличается от основного; иначе None."""
    if _override is not None:
        return _fallback_override
    s = get_settings()
    name = s.ai_fallback_provider.strip().lower()
    if not name or name == s.ai_provider:
        return None
    try:
        fb = _build(name)
    except ProviderError:
        return None
    if primary is not None and primary.name == fb.name:
        return None
    return fb if fb.configured() else None
