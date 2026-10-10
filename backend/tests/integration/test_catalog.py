from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import create_org, csrf_headers, join, register, setup_org

NewClient = Callable[[], TestClient]

# роль: (читает справочники, управляет справочниками) — docs/06-stage2-plan.md §4
CATALOG = {
    "owner": (True, True),
    "org_admin": (True, True),
    "accountant": (True, True),
    "storekeeper": (True, False),
    "sales_purchase_manager": (True, True),
    "document_operator": (True, False),
    "auditor": (True, False),
}


def currencies(c: TestClient, org: str) -> list[dict[str, Any]]:
    r = c.get(f"/api/v1/organizations/{org}/currencies")
    assert r.status_code == 200, r.text
    out: list[dict[str, Any]] = r.json()
    return out


def put_currency(c: TestClient, org: str, code: str, enabled: bool) -> Any:
    return c.put(
        f"/api/v1/organizations/{org}/currencies/{code}",
        json={"enabled": enabled},
        headers=csrf_headers(c),
    )


def units(c: TestClient, org: str) -> list[dict[str, Any]]:
    r = c.get(f"/api/v1/organizations/{org}/units")
    assert r.status_code == 200, r.text
    out: list[dict[str, Any]] = r.json()
    return out


def add_unit(c: TestClient, org: str, **body: Any) -> Any:
    return c.post(f"/api/v1/organizations/{org}/units", json=body, headers=csrf_headers(c))


def patch_unit(c: TestClient, org: str, unit_id: str, **body: Any) -> Any:
    return c.patch(
        f"/api/v1/organizations/{org}/units/{unit_id}", json=body, headers=csrf_headers(c)
    )


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


@pytest.mark.parametrize("role", list(CATALOG))
def test_catalog_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage = CATALOG[role]
    assert (actor.get(f"/api/v1/organizations/{org}/currencies").status_code == 200) is can_read
    assert (actor.get(f"/api/v1/organizations/{org}/units").status_code == 200) is can_read
    assert put_currency(actor, org, "USD", True).status_code == (200 if can_manage else 403)
    assert add_unit(actor, org, code="uq", name_en="x").status_code == (201 if can_manage else 403)


# ---------------- валюты ----------------


def test_currencies_default_and_toggle(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    cur = currencies(owner, org)
    assert next(c["code"] for c in cur) == "UZS" and len(cur) == 6
    assert cur[0]["is_base"] is True and cur[0]["enabled"] is True and cur[0]["minor_units"] == 2
    assert cur[0]["name_uz"] == "Oʻzbekiston soʻmi" and cur[0]["name_en"] == "Uzbekistani som"
    assert {c["code"] for c in cur if c["enabled"]} == {"UZS"}

    r = put_currency(owner, org, "usd", True)  # регистр кода не важен
    assert r.status_code == 200
    assert {c["code"] for c in r.json() if c["enabled"]} == {"UZS", "USD"}
    put_currency(owner, org, "USD", True)  # повтор — без новой записи в аудите
    assert actions(admin_engine).count("currency.enabled") == 1
    off = put_currency(owner, org, "USD", False)
    assert {c["code"] for c in off.json() if c["enabled"]} == {"UZS"}
    assert actions(admin_engine).count("currency.disabled") == 1


def test_base_currency_cannot_be_disabled_and_unknown_code(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    locked = put_currency(owner, org, "UZS", False)
    assert locked.status_code == 409 and locked.json()["code"] == "base_currency_locked"
    assert put_currency(owner, org, "XXX", True).status_code == 404
    assert put_currency(owner, org, "TOOLONG", True).status_code == 422


def test_organization_with_other_base_currency(new_client: NewClient, admin_engine: Engine) -> None:
    c = new_client()
    register(c, "usd@example.uz")
    org = create_org(c, "USD Inc", base_currency="USD").json()["id"]
    cur = {x["code"]: x for x in currencies(c, org)}
    assert cur["USD"]["is_base"] and cur["USD"]["enabled"] and not cur["UZS"]["enabled"]
    unknown = create_org(c, "Bad Inc", base_currency="XXX")
    assert unknown.status_code == 422
    assert unknown.json()["details"][0]["code"] == "currency_unknown"
    with admin_engine.connect() as conn:
        bases = conn.execute(
            text(
                "SELECT organization_id, count(*) FROM organization_currency WHERE is_base GROUP BY 1"
            )
        ).all()
    assert all(n == 1 for _, n in bases) and len(bases) == 1


# ---------------- единицы измерения ----------------


def test_system_units_visible_everywhere(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    sys_units = units(owner, org)
    assert [u["code"] for u in sys_units] == ["g", "kg", "l", "m", "pcs"]
    pcs = next(u for u in sys_units if u["code"] == "pcs")
    assert (pcs["name_uz"], pcs["name_ru"], pcs["name_en"]) == ("dona", "шт", "pcs")
    assert all(u["is_system"] and u["is_active"] for u in sys_units)


def test_create_and_update_custom_unit(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    created = add_unit(owner, org, code=" box ", name_ru="Коробка", name_en="Box")
    assert created.status_code == 201
    u = created.json()
    assert u["code"] == "box" and u["is_system"] is False and u["name_uz"] is None
    assert [x["code"] for x in units(owner, org)][-1] == "box"  # свои — после системных

    upd = patch_unit(owner, org, u["id"], name_uz="Quti", name_en=None)
    assert upd.status_code == 200
    assert (upd.json()["name_uz"], upd.json()["name_en"], upd.json()["code"]) == (
        "Quti",
        None,
        "box",
    )
    off = patch_unit(owner, org, u["id"], is_active=False)
    assert off.json()["is_active"] is False
    assert patch_unit(owner, org, u["id"], is_active=True).json()["is_active"] is True

    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT before, after FROM audit_event WHERE action='unit.updated' ORDER BY occurred_at"
            )
        ).all()
    assert rows[0][0]["name_uz"] is None and rows[0][1]["name_uz"] == "Quti"
    assert rows[1][1]["is_active"] is False
    assert "unit.created" in actions(admin_engine)


def test_unit_validation_and_uniqueness(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    no_name = add_unit(owner, org, code="x1")
    assert no_name.status_code == 422
    assert no_name.json()["details"][0]["code"] == "unit_name_required"
    bad_code = add_unit(owner, org, code="!!bad", name_en="x")
    assert (
        bad_code.status_code == 422 and bad_code.json()["details"][0]["code"] == "unit_code_invalid"
    )
    assert add_unit(owner, org, code="x" * 17, name_en="x").status_code == 422

    assert add_unit(owner, org, code="Box", name_en="Box").status_code == 201
    dup = add_unit(owner, org, code="BOX", name_en="again")
    assert dup.status_code == 409 and dup.json()["code"] == "unit_code_taken"
    assert dup.json()["details"][0] == {
        "field": "code",
        "code": "unit_code_taken",
        "message": "Код уже используется.",
    }
    system_clash = add_unit(owner, org, code="PCS", name_en="mine")
    assert system_clash.status_code == 409  # нельзя «перекрыть» системную единицу


def test_system_unit_is_readonly_and_clearing_names_rejected(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    pcs = next(u for u in units(owner, org) if u["code"] == "pcs")
    r = patch_unit(owner, org, pcs["id"], is_active=False)
    assert r.status_code == 409 and r.json()["code"] == "system_unit_readonly"
    mine = add_unit(owner, org, code="box", name_en="Box").json()
    cleared = patch_unit(owner, org, mine["id"], name_en=None)
    assert cleared.status_code == 422
    assert cleared.json()["details"][0]["code"] == "unit_name_required"
    assert next(u for u in units(owner, org) if u["code"] == "box")["name_en"] == "Box"


def test_units_isolated_between_orgs(new_client: NewClient) -> None:
    a, org_a = setup_org(new_client, "a@example.uz")
    b, org_b = setup_org(new_client, "b@example.uz")
    ua = add_unit(a, org_a, code="box", name_en="A box").json()
    assert "box" not in [u["code"] for u in units(b, org_b)]
    assert (
        add_unit(b, org_b, code="box", name_en="B box").status_code == 201
    )  # тот же код в другой орг.
    assert patch_unit(b, org_b, ua["id"], is_active=False).status_code == 404  # чужая единица
    assert patch_unit(b, org_a, ua["id"], is_active=False).status_code == 404  # чужая организация
    assert next(u for u in units(a, org_a) if u["code"] == "box")["is_active"] is True
    assert a.get(f"/api/v1/organizations/{org_b}/units").status_code == 404
    assert a.get(f"/api/v1/organizations/{org_b}/currencies").status_code == 404
