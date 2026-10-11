"""Этап 9.3: второй фактор (TOTP) — алгоритм по RFC 6238, настройка, вход, коды восстановления, политика."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.identity import totp
from app.platform.config import get_settings
from tests.helpers import PASSWORD, create_org, csrf_headers, join, register, setup_org
from tests.integration.test_bank import NewClient


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "app_secret_key", "k" * 40)
    yield


def post(c: Any, path: str, **body: Any) -> Any:
    return c.post("/api/v1/auth" + path, json=body or None, headers=csrf_headers(c))


def next_code(secret: str) -> str:
    """Код следующего интервала (допустимое окно ±1): не совпадает с уже использованным."""
    return totp.hotp(secret, int(time.time() // totp.STEP) + 1)


def enroll(c: Any) -> tuple[str, list[str]]:
    secret = post(c, "/mfa/setup").json()["secret"]
    r = post(c, "/mfa/enable", code=totp.at(secret))
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def test_totp_matches_rfc6238_vectors() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
    # векторы RFC 6238 (SHA-1), последние 6 цифр восьмизначных кодов
    for ts, code in (
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
    ):
        assert totp.at(secret, ts) == code
    assert totp.verify(secret, "287082", None, ts=59) == 1
    assert totp.verify(secret, "287082", 1, ts=59) is None  # тот же интервал повторно нельзя
    assert totp.verify(secret, "287082", None, ts=59 + 3 * 30) is None  # вне окна
    assert (
        totp.verify(secret, "12345", None, ts=59) is None
        and totp.verify(secret, "abcdef", None, ts=59) is None
    )
    assert totp.otpauth_uri("ABC", "a@b.uz", "Hisob AI").startswith(
        "otpauth://totp/Hisob%20AI:a%40b.uz?secret=ABC"
    )


def test_unavailable_without_server_key(new_client: NewClient) -> None:
    c = new_client()
    register(c, "u@example.uz")
    st = c.get("/api/v1/auth/mfa").json()
    assert st["available"] is False and st["enabled"] is False
    r = post(c, "/mfa/setup")
    assert r.status_code == 409 and r.json()["code"] == "mfa_unavailable"


def login_as(new_client: NewClient, email: str, **extra: Any) -> Any:
    return new_client().post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD, **extra}
    )


def test_enroll_login_replay_and_recovery(
    new_client: NewClient, key: None, admin_engine: Engine
) -> None:
    c = new_client()
    register(c, "mfa@example.uz")
    assert post(c, "/mfa/enable", code="123456").json()["code"] == "mfa_not_pending"
    setup = post(c, "/mfa/setup").json()
    secret = setup["secret"]
    assert (
        setup["otpauth_uri"].startswith("otpauth://totp/")
        and c.get("/api/v1/auth/mfa").json()["pending"] is True
    )
    bad = post(c, "/mfa/enable", code="000000")
    assert bad.status_code == 422 and bad.json()["code"] == "mfa_invalid_code"
    ok = post(c, "/mfa/enable", code=totp.at(secret))
    codes = ok.json()["recovery_codes"]
    assert len(codes) == 8 and len(set(codes)) == 8
    st = c.get("/api/v1/auth/mfa").json()
    assert st["enabled"] is True and st["recovery_codes_left"] == 8
    with admin_engine.connect() as conn:
        enc = conn.execute(text("SELECT secret_enc FROM user_mfa")).scalar()
        hashes = [r[0] for r in conn.execute(text("SELECT code_hash FROM mfa_recovery_code"))]
    assert secret.encode() not in bytes(enc) and all(
        code.replace("-", "") not in " ".join(hashes) for code in codes
    )
    assert post(c, "/mfa/setup").json()["code"] == "mfa_already_enabled"

    post(c, "/logout")
    need = login_as(new_client, "mfa@example.uz")
    assert need.status_code == 401 and need.json()["code"] == "mfa_required"
    assert (
        login_as(new_client, "mfa@example.uz", mfa_code="111111").json()["code"]
        == "mfa_invalid_code"
    )
    good = next_code(secret)
    assert login_as(new_client, "mfa@example.uz", mfa_code=good).status_code == 200
    assert (
        login_as(new_client, "mfa@example.uz", mfa_code=good).json()["code"] == "mfa_invalid_code"
    )  # повтор того же кода отклонён
    # код восстановления: работает один раз, пробелы и регистр не важны
    assert login_as(new_client, "mfa@example.uz", mfa_code=codes[0].upper()).status_code == 200
    assert (
        login_as(new_client, "mfa@example.uz", mfa_code=codes[0]).json()["code"]
        == "mfa_invalid_code"
    )
    with admin_engine.connect() as conn:
        acts = [r[0] for r in conn.execute(text("SELECT action FROM audit_event"))]
    for a in (
        "auth.mfa_setup_started",
        "auth.mfa_enabled",
        "auth.mfa_failed",
        "auth.mfa_recovery_used",
    ):
        assert a in acts, a


def test_wrong_codes_are_throttled(new_client: NewClient, key: None) -> None:
    c = new_client()
    register(c, "thr@example.uz")
    enroll(c)
    post(c, "/logout")
    fresh = new_client()
    codes = [
        fresh.post(
            "/api/v1/auth/login",
            json={"email": "thr@example.uz", "password": PASSWORD, "mfa_code": "000000"},
        ).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429  # перебор шестизначного кода ограничен


def test_disable_and_regenerate_need_password_and_code(new_client: NewClient, key: None) -> None:
    c = new_client()
    register(c, "dis@example.uz")
    secret, codes = enroll(c)
    assert (
        post(c, "/mfa/disable", password="неверный пароль 123", code=next_code(secret)).json()[
            "code"
        ]
        == "invalid_current_password"
    )
    assert (
        post(c, "/mfa/disable", password=PASSWORD, code="000000").json()["code"]
        == "mfa_invalid_code"
    )
    regen = post(c, "/mfa/recovery-codes", password=PASSWORD, code=next_code(secret))
    new_codes = regen.json()["recovery_codes"]
    assert regen.status_code == 200 and set(new_codes).isdisjoint(codes)
    assert (
        post(c, "/mfa/disable", password=PASSWORD, code=new_codes[0]).status_code == 204
    )  # код восстановления подходит
    assert c.get("/api/v1/auth/mfa").json()["enabled"] is False
    other = new_client()
    assert (
        other.post(
            "/api/v1/auth/login", json={"email": "dis@example.uz", "password": PASSWORD}
        ).status_code
        == 200
    )


def test_admin_policy_blocks_admins_without_mfa(
    new_client: NewClient, key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, org = setup_org(new_client)
    acc = join(new_client, owner, org, "acc@example.uz", "accountant")
    assert owner.get(f"/api/v1/organizations/{org}/members").status_code == 200
    monkeypatch.setattr(get_settings(), "mfa_required_for_admins", True)
    blocked = owner.get(f"/api/v1/organizations/{org}/members")
    assert blocked.status_code == 403 and blocked.json()["code"] == "mfa_required_for_admins"
    assert (
        acc.get(f"/api/v1/organizations/{org}/members").status_code == 200
    )  # не администратор — не затронут
    assert (
        owner.get("/api/v1/auth/mfa").json()["required_for_admins"] is True
    )  # страница безопасности доступна
    enroll(owner)
    assert owner.get(f"/api/v1/organizations/{org}/members").status_code == 200


def test_create_org_still_works_after_mfa_setup(new_client: NewClient, key: None) -> None:
    c = new_client()
    register(c, "later@example.uz")
    enroll(c)
    assert create_org(c, "ООО Позже").status_code == 201
