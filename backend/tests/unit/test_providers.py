import base64
import io
import json
import os
import zipfile
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from openpyxl import Workbook

from app.modules.documents.extraction import schema
from app.modules.documents.extraction.inputs import InputError, ProviderInput, build_input
from app.modules.documents.extraction.providers import (
    DeepSeekProvider,
    OpenAIProvider,
    ProviderError,
)
from tests.files import PDF, png_bytes

KEY = "sk-secret-test-key-123"


def payload() -> dict[str, Any]:
    f = {"value": None, "confidence": None, "evidence": None}
    d: dict[str, Any] = {k: dict(f) for k in schema.HEADER_FIELDS}
    d["number"] = {"value": "77", "confidence": 0.9, "evidence": "No 77"}
    d["lines"] = []
    return d


def client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def openai(handler: Any) -> OpenAIProvider:
    return OpenAIProvider(
        api_key=KEY,
        model="gpt-test",
        base_url="https://api.example.test/",
        timeout=5,
        client=client(handler),
    )


def deepseek(handler: Any) -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key=KEY,
        model="ds-test",
        base_url="https://ds.example.test",
        timeout=5,
        client=client(handler),
    )


def oa_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp_1",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(payload())}],
                }
            ],
            "usage": {"input_tokens": 1200, "output_tokens": 300},
        },
    )


# ---------- входные данные ----------


def test_build_input_kinds() -> None:
    img = build_input("image/png", png_bytes(), "a.png")
    assert img.kind == "image" and img.data_url().startswith("data:image/png;base64,")
    assert base64.b64decode(img.data_url().split(",", 1)[1]) == png_bytes()
    assert build_input("application/pdf", PDF, "a.pdf").kind == "pdf"
    csv = build_input("text/csv", "номер;сумма\n1;100\n".encode("cp1251"), "a.csv")
    assert csv.kind == "text" and "номер;сумма" in (csv.text or "")
    assert (csv.text or "").startswith("<<<DOCUMENT DATA START>>>") and (csv.text or "").endswith(
        "<<<DOCUMENT DATA END>>>"
    )


def test_xlsx_to_text_and_limits() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Счёт"
    ws.append(["Наименование", "Кол-во", "Сумма"])
    ws.append(["Молоко", 2, 200.5])
    for i in range(600):
        ws.append([f"row{i}", i, i])
    buf = io.BytesIO()
    wb.save(buf)
    text = (
        build_input(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            buf.getvalue(),
            "a.xlsx",
        ).text
        or ""
    )
    assert (
        "Sheet: Счёт" in text
        and "Молоко\t2\t200.5" in text
        and "rows truncated" in text
        and "row599" not in text
    )


def test_build_input_errors() -> None:
    with pytest.raises(InputError):
        build_input("application/zip", b"x", "a.zip")
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("x.txt", "x")
    with pytest.raises(InputError):
        build_input(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            bad.getvalue(),
            "a.xlsx",
        )


# ---------- OpenAI ----------


def test_openai_request_shape_for_each_input_kind() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example.test/v1/responses"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        seen.append(json.loads(request.content))
        assert KEY not in request.content.decode()  # ключ только в заголовке
        return oa_ok(request)

    p = openai(handler)
    res = p.extract(build_input("image/png", png_bytes(), "a.png"))
    p.extract(build_input("application/pdf", PDF, "a.pdf"))
    p.extract(build_input("text/csv", b"a;b\n", "a.csv"))

    img, pdf, txt = seen
    for body in seen:
        assert (
            body["model"] == "gpt-test" and body["store"] is False and body["max_output_tokens"] > 0
        )
        fmt = body["text"]["format"]
        assert (
            fmt["type"] == "json_schema"
            and fmt["strict"] is True
            and fmt["schema"] == schema.json_schema()
        )
        assert "untrusted DATA" in body["instructions"]
    parts = img["input"][0]["content"]
    assert [x["type"] for x in parts] == ["input_text", "input_image"] and parts[1][
        "image_url"
    ].startswith("data:image/png;base64,")
    pparts = pdf["input"][0]["content"]
    assert pparts[0]["type"] == "input_file" and pparts[0]["filename"] == "a.pdf"
    assert pparts[0]["file_data"].startswith("data:application/pdf;base64,")
    assert "DOCUMENT DATA START" in txt["input"][0]["content"][0]["text"]
    assert (
        res.payload["number"]["value"] == "77"
        and (res.tokens_in, res.tokens_out) == (1200, 300)
        and res.request_id == "resp_1"
    )


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "provider_auth", False),
        (403, "provider_auth", False),
        (404, "provider_model_unavailable", False),
        (400, "provider_bad_request", False),
        (429, "provider_rate_limited", True),
        (500, "provider_unavailable", True),
        (503, "provider_unavailable", True),
    ],
)
def test_openai_http_errors_are_classified(status: int, code: str, retryable: bool) -> None:
    p = openai(lambda r: httpx.Response(status, json={"error": {"message": "boom detail"}}))
    with pytest.raises(ProviderError) as exc:
        p.extract(build_input("image/png", png_bytes(), "a.png"))
    assert exc.value.code == code and exc.value.retryable is retryable
    assert KEY not in exc.value.message


def test_openai_network_problems_are_retryable() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    for h, code in ((timeout, "provider_timeout"), (refused, "provider_unavailable")):
        with pytest.raises(ProviderError) as exc:
            openai(h).extract(build_input("image/png", png_bytes(), "a.png"))
        assert exc.value.code == code and exc.value.retryable


def test_openai_bad_outputs() -> None:
    def msg(text: str) -> dict[str, Any]:
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        }

    def case(body: dict[str, Any]) -> ProviderError:
        p = openai(lambda r: httpx.Response(200, json=body))
        with pytest.raises(ProviderError) as exc:
            p.extract(build_input("image/png", png_bytes(), "a.png"))
        return exc.value

    assert case(msg("not json")).code == "invalid_provider_output"
    assert case(msg("[1, 2]")).code == "invalid_provider_output"
    assert case({"status": "completed", "output": []}).code == "provider_empty_output"
    assert case({"status": "incomplete", "output": []}).code == "provider_incomplete"
    refusal = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
    }
    assert case(refusal).code == "provider_refusal" and not case(refusal).retryable


def test_openai_not_configured() -> None:
    p = OpenAIProvider(
        api_key="", model="", base_url="https://x.test", timeout=5, client=client(oa_ok)
    )
    assert not p.configured()
    with pytest.raises(ProviderError) as exc:
        p.extract(build_input("image/png", png_bytes(), "a.png"))
    assert exc.value.code == "provider_not_configured"


# ---------- DeepSeek ----------


def ds_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "cmpl-1",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload())}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 200},
        },
    )


def test_deepseek_request_shape_and_result() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ds.example.test/chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        seen.append(json.loads(request.content))
        return ds_ok(request)

    p = deepseek(handler)
    res = p.extract(build_input("image/jpeg", b"\xff\xd8\xff data", "a.jpg"))
    p.extract(build_input("text/csv", b"a;b\n", "a.csv"))
    img, txt = seen
    for body in seen:
        assert (
            body["response_format"] == {"type": "json_object"}
            and body["stream"] is False
            and body["max_tokens"] > 0
        )
        system = body["messages"][0]["content"]
        assert (
            "json" in system.lower() and '"number"' in system
        )  # требование режима JSON: слово json и пример формы
    uparts = img["messages"][1]["content"]
    assert uparts[1]["type"] == "image_url" and uparts[1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert "DOCUMENT DATA START" in txt["messages"][1]["content"][0]["text"]
    assert res.payload["number"]["value"] == "77" and (res.tokens_in, res.tokens_out) == (800, 200)


def test_deepseek_pdf_is_honestly_unsupported() -> None:
    called = False

    def handler(r: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return ds_ok(r)

    with pytest.raises(ProviderError) as exc:
        deepseek(handler).extract(build_input("application/pdf", PDF, "a.pdf"))
    assert exc.value.code == "provider_unsupported_input" and not exc.value.retryable and not called


def test_deepseek_errors_and_empty_content() -> None:
    def run(resp: httpx.Response) -> ProviderError:
        with pytest.raises(ProviderError) as exc:
            deepseek(lambda r: resp).extract(build_input("image/png", png_bytes(), "a.png"))
        return exc.value

    assert run(httpx.Response(401, json={"error": {"message": "bad key"}})).code == "provider_auth"
    assert run(httpx.Response(429, json={})).retryable
    empty = run(
        httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
        )
    )
    assert (
        empty.code == "provider_empty_output" and empty.retryable
    )  # документация: изредка приходит пустой ответ
    assert (
        run(
            httpx.Response(
                200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}
            )
        ).code
        == "provider_incomplete"
    )
    assert run(httpx.Response(200, json={"choices": []})).code == "provider_empty_output"
    assert run(httpx.Response(200, text="<html>")).code == "provider_invalid_response"


def test_inputs_never_contain_secrets_in_text_input() -> None:
    inp = ProviderInput("text", "text/plain", "a.csv", text="x")
    body = openai(oa_ok).build_body(inp)
    assert KEY not in json.dumps(body)


# ---------- выбор основного и запасного ----------


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    from app.platform.config import get_settings

    for k in (
        "AI_PROVIDER",
        "AI_FALLBACK_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def test_primary_openai_fallback_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.documents.extraction.providers import get_fallback_provider, get_provider

    _settings(
        monkeypatch,
        AI_PROVIDER="openai",
        AI_FALLBACK_PROVIDER="deepseek",
        OPENAI_API_KEY="k1",
        OPENAI_MODEL="m1",
        DEEPSEEK_API_KEY="k2",
        DEEPSEEK_MODEL="m2",
    )
    primary = get_provider()
    fb = get_fallback_provider(primary)
    assert primary.name == "openai" and fb is not None and fb.name == "deepseek"


def test_unconfigured_primary_falls_back_and_no_self_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.documents.extraction.providers import get_fallback_provider, get_provider

    _settings(
        monkeypatch,
        AI_PROVIDER="openai",
        AI_FALLBACK_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="k2",
        DEEPSEEK_MODEL="m2",
    )
    assert get_provider().name == "deepseek"  # основной не настроен — работает запасной
    _settings(monkeypatch, AI_PROVIDER="openai", AI_FALLBACK_PROVIDER="openai", OPENAI_API_KEY="k")
    assert get_fallback_provider(get_provider()) is None
    _settings(monkeypatch, AI_PROVIDER="openai", OPENAI_API_KEY="k", OPENAI_MODEL="m")
    assert get_fallback_provider(get_provider()) is None  # запасной не задан
    _settings(
        monkeypatch,
        AI_PROVIDER="openai",
        AI_FALLBACK_PROVIDER="deepseek",
        OPENAI_API_KEY="k",
        OPENAI_MODEL="m",
    )
    assert get_fallback_provider(get_provider()) is None  # запасной не настроен
    _settings(monkeypatch)


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    yield
    from app.platform.config import get_settings

    get_settings.cache_clear()
