"""Запасной провайдер: срабатывает только при отказе основного, результат помечается, ошибки файла не маскируются."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.documents.extraction.providers import (
    MockProvider,
    ProviderError,
    set_provider_for_tests,
)
from tests.ai_helpers import good_payload, set_runtime
from tests.files import png_bytes
from tests.integration.test_extraction import NewClient, extract, make_org, new_doc


class FallbackMock(MockProvider):
    name = "mock-fallback"


def failing(code: str, retryable: bool = False) -> Any:
    def handler(_: Any) -> dict[str, Any]:
        raise ProviderError(code, "boom", retryable=retryable)

    return handler


@pytest.fixture
def pair(admin_engine: Engine) -> Iterator[Any]:
    def make(primary: Any, fallback: Any) -> tuple[MockProvider, FallbackMock]:
        p, f = MockProvider(primary), FallbackMock(fallback, model="fb-1")
        set_provider_for_tests(p, f)
        set_runtime(admin_engine)
        return p, f

    yield make
    set_provider_for_tests(None)


def job(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = doc["extraction"]["job"]
    return out


def test_fallback_used_when_primary_is_down(new_client: NewClient, pair: Any) -> None:
    owner, org = make_org(new_client)
    p, f = pair(failing("provider_unavailable", retryable=True), good_payload())
    did = new_doc(owner, org, data=png_bytes(color="red"), name="a.png")
    d = extract(owner, org, did, final=False)  # без запасного это был бы повтор позже
    assert len(p.calls) == 1 and len(f.calls) == 1
    assert job(d)["status"] == "succeeded"
    assert (job(d)["provider"], job(d)["model"]) == ("mock-fallback", "fb-1")
    assert d["number"] == "INV-100" and d["status"] == "NEEDS_REVIEW"


def test_fallback_recorded_in_audit(new_client: NewClient, pair: Any, admin_engine: Engine) -> None:
    owner, org = make_org(new_client)
    pair(failing("provider_auth"), good_payload())
    extract(owner, org, new_doc(owner, org, data=png_bytes(color="blue"), name="b.png"))
    with admin_engine.connect() as c:
        after = c.execute(
            text("SELECT after FROM audit_event WHERE action = 'document.extracted'")
        ).scalar_one()
    assert after["fallback_from"] == "mock" and after["primary_error"] == "provider_auth"


def test_primary_success_never_touches_fallback(new_client: NewClient, pair: Any) -> None:
    owner, org = make_org(new_client)
    p, f = pair(good_payload(), good_payload())
    d = extract(owner, org, new_doc(owner, org, data=png_bytes(color="green"), name="c.png"))
    assert len(p.calls) == 1 and f.calls == [] and job(d)["provider"] == "mock"


def test_both_fail_reports_primary_error(new_client: NewClient, pair: Any) -> None:
    owner, org = make_org(new_client)
    pair(failing("provider_auth"), failing("provider_timeout", retryable=True))
    d = extract(owner, org, new_doc(owner, org, data=png_bytes(color="white"), name="d.png"))
    assert job(d)["status"] == "failed" and job(d)["error_code"] == "provider_auth"
    assert "provider_timeout" in (job(d)["error_message"] or "")
    assert d["status"] == "NEEDS_REVIEW"


def test_fallback_unsupported_input_keeps_primary_error(new_client: NewClient, pair: Any) -> None:
    owner, org = make_org(new_client)
    pair(failing("provider_rate_limited"), failing("provider_unsupported_input"))
    d = extract(owner, org, new_doc(owner, org, data=png_bytes(color="black"), name="e.png"))
    assert job(d)["error_code"] == "provider_rate_limited"
