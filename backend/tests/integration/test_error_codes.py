"""Ошибки полей несут устойчивый code: по нему клиент переводит сообщение на нужный язык."""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import PASSWORD, create_org, csrf_headers, invite, register, setup_org

NewClient = Callable[[], TestClient]


def codes(resp: Any) -> list[tuple[str, str]]:
    return [(d["field"], d["code"]) for d in resp.json()["details"]]


def test_register_error_codes(new_client: NewClient) -> None:
    c = new_client()
    weak = register(c, "a@example.uz", password="short")
    assert codes(weak) == [("password", "password_too_short")]
    assert weak.json()["details"][0]["params"] == {"min": 10}
    assert register(c, "a@example.uz").status_code == 201
    taken = register(new_client(), "A@example.uz")
    assert codes(taken) == [("email", "email_taken")]
    missing = new_client().post("/api/v1/auth/register", json={"email": "b@example.uz"})
    assert ("password", "required") in codes(missing) and ("full_name", "required") in codes(
        missing
    )


def test_organization_error_codes(new_client: NewClient) -> None:
    c = new_client()
    register(c, "o@example.uz")
    assert codes(create_org(c, "A")) == [("name", "too_short")]
    assert codes(create_org(c, "Valid", tin="12ab")) == [("tin", "tin_invalid")]
    assert codes(create_org(c, "Valid", timezone="Mars/Base")) == [("timezone", "timezone_unknown")]
    assert codes(create_org(c, "Valid", base_currency="uzs1")) == [
        ("base_currency", "currency_invalid")
    ]


def test_password_change_and_role_error_codes(new_client: NewClient) -> None:
    c = new_client()
    register(c, "p@example.uz")
    h = csrf_headers(c)
    wrong = c.post(
        "/api/v1/auth/change-password",
        json={"current_password": "x" * 12, "new_password": "y" * 12},
        headers=h,
    )
    assert codes(wrong) == [("current_password", "invalid_current_password")]
    short = c.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=h,
    )
    assert codes(short) == [("new_password", "password_too_short")]
    same = c.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=h,
    )
    assert codes(same) == [("new_password", "password_same_as_current")]

    owner, org = setup_org(new_client)
    assert codes(invite(owner, org, "n@example.uz", "nope")) == [("role_code", "unknown_role")]
    bad_range = owner.get(
        f"/api/v1/organizations/{org}/audit",
        params={"date_from": "2026-12-02", "date_to": "2026-12-01"},
    )
    assert codes(bad_range) == [("date_from", "date_range_invalid")]
