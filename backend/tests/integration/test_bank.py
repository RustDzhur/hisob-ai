"""Срез 6.1: банковские счета, профиль импорта, предпросмотр и идемпотентный импорт выписок, операции."""

import io
import json
import threading
import zipfile
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.helpers import csrf_headers, join, setup_org

NewClient = Callable[[], TestClient]

# роль: (читает, управляет счетами, загружает выписки) — R13, переспросить в конце проекта
CAN = {
    "owner": (True, True, True),
    "org_admin": (True, True, True),
    "accountant": (True, True, True),
    "auditor": (True, False, False),
    "storekeeper": (False, False, False),
    "sales_purchase_manager": (False, False, False),
    "document_operator": (False, False, False),
}

PROFILE: dict[str, Any] = {
    "format": "csv",
    "delimiter": ";",
    "encoding": "utf-8",
    "header_row": 1,
    "date_column": "Дата",
    "date_format": "%d.%m.%Y",
    "amount_mode": "signed",
    "amount_column": "Сумма",
    "decimal_separator": ",",
    "thousands_separator": " ",
    "description_column": "Назначение",
    "reference_column": "Номер",
    "counterparty_name_column": "Контрагент",
    "counterparty_tin_column": "ИНН",
}
HEADER = "Дата;Сумма;Назначение;Номер;Контрагент;ИНН\n"


def base(org: str) -> str:
    return f"/api/v1/organizations/{org}/bank"


def h(c: TestClient) -> dict[str, str]:
    return csrf_headers(c)


def statement(*rows: str) -> bytes:
    return (HEADER + "".join(r + "\n" for r in rows)).encode("utf-8")


def new_account(
    c: TestClient, org: str, number: str = "20208000100200300400", **extra: Any
) -> dict[str, Any]:
    r = c.post(
        base(org) + "/accounts",
        json={
            "name": "Основной",
            "account_number": number,
            "currency": "UZS",
            "bank_name": "Тест-банк",
            **extra,
        },
        headers=h(c),
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def with_profile(c: TestClient, org: str, **account_extra: Any) -> dict[str, Any]:
    a = new_account(c, org, **account_extra)
    r = c.put(base(org) + f"/accounts/{a['id']}/profile", json=PROFILE, headers=h(c))
    assert r.status_code == 200, r.text
    return a


def upload(
    c: TestClient,
    org: str,
    aid: str,
    data: bytes,
    name: str = "stmt.csv",
    path: str = "imports",
    **form: Any,
) -> Any:
    return c.post(
        base(org) + f"/accounts/{aid}/{path}",
        files={"file": (name, data, "application/octet-stream")},
        data={k: (json.dumps(v) if isinstance(v, dict) else str(v)) for k, v in form.items()},
        headers=h(c),
    )


def txns(c: TestClient, org: str, **params: Any) -> list[dict[str, Any]]:
    r = c.get(base(org) + "/transactions", params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


def actions(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [
            r[0] for r in c.execute(text("SELECT action FROM audit_event ORDER BY occurred_at, id"))
        ]


# ---------------- права ----------------


@pytest.mark.parametrize("role", list(CAN))
def test_permission_matrix(new_client: NewClient, role: str) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    actor = owner if role == "owner" else join(new_client, owner, org, f"{role}@example.uz", role)
    can_read, can_manage, can_import = CAN[role]
    assert (actor.get(base(org) + "/accounts").status_code == 200) is can_read
    assert (actor.get(base(org) + "/transactions").status_code == 200) is can_read
    assert (actor.get(base(org) + f"/accounts/{a['id']}/imports").status_code == 200) is can_read
    mk = actor.post(
        base(org) + "/accounts",
        json={
            "name": "X",
            "account_number": f"ACC{role.replace('_', '')[:6]}12345".upper(),
            "currency": "UZS",
        },
        headers=h(actor),
    )
    assert mk.status_code == (201 if can_manage else 403)
    assert actor.put(
        base(org) + f"/accounts/{a['id']}/profile", json=PROFILE, headers=h(actor)
    ).status_code == (200 if can_manage else 403)
    data = statement(f"01.10.2026;100,00;Тест {role};R1;ООО;123456789")
    assert upload(actor, org, a["id"], data, path="preview").status_code == (
        200 if can_import else 403
    )
    assert upload(actor, org, a["id"], data).status_code == (201 if can_import else 403)


# ---------------- счета и профиль ----------------


def test_accounts_and_profile(new_client: NewClient, admin_engine: Engine) -> None:
    owner, org = setup_org(new_client)
    a = new_account(owner, org, "2020 8000 1002 0030 0400")  # пробелы убираются, регистр приводится
    assert (a["account_number"], a["currency"], a["has_profile"], a["transactions_count"]) == (
        "20208000100200300400",
        "UZS",
        False,
        0,
    )
    dup = owner.post(
        base(org) + "/accounts",
        json={"name": "Дубль", "account_number": "20208000100200300400", "currency": "UZS"},
        headers=h(owner),
    )
    assert dup.status_code == 409 and dup.json()["code"] == "bank_account_taken"
    for body in (
        {"name": "", "account_number": "12345", "currency": "UZS"},
        {"name": "X", "account_number": "12", "currency": "UZS"},
        {"name": "X", "account_number": "ABC$%^&*", "currency": "UZS"},
        {"name": "X", "account_number": "12345", "currency": "ZZZ"},
    ):
        assert (
            owner.post(base(org) + "/accounts", json=body, headers=h(owner)).status_code == 422
        ), body
    assert owner.post(
        base(org) + "/accounts",
        json={"name": "USD", "account_number": "USD12345", "currency": "EUR"},
        headers=h(owner),
    ).json()["code"] in ("currency_not_enabled", "currency_unknown")
    upd = owner.patch(
        base(org) + f"/accounts/{a['id']}",
        json={"name": "Расчётный", "notes": "заметка"},
        headers=h(owner),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "Расчётный"
    assert owner.get(base(org) + f"/accounts/{a['id']}/profile").json() is None
    bad = dict(PROFILE, date_format="%Y")
    r = owner.put(base(org) + f"/accounts/{a['id']}/profile", json=bad, headers=h(owner))
    assert r.status_code == 422
    assert (
        owner.put(
            base(org) + f"/accounts/{a['id']}/profile",
            json=dict(PROFILE, amount_column=None),
            headers=h(owner),
        ).status_code
        == 422
    )
    assert (
        owner.put(
            base(org) + f"/accounts/{a['id']}/profile", json=PROFILE, headers=h(owner)
        ).status_code
        == 200
    )
    assert owner.get(base(org) + f"/accounts/{a['id']}/profile").json()["amount_column"] == "Сумма"
    assert owner.get(base(org) + f"/accounts/{a['id']}").json()["has_profile"] is True
    arch = owner.post(base(org) + f"/accounts/{a['id']}/archive", headers=h(owner))
    assert arch.json()["archived"] is True
    assert owner.get(base(org) + "/accounts").json()["total"] == 0
    assert owner.get(base(org) + "/accounts", params={"status": "archived"}).json()["total"] == 1
    assert (
        owner.patch(
            base(org) + f"/accounts/{a['id']}", json={"name": "x"}, headers=h(owner)
        ).json()["code"]
        == "bank_account_archived"
    )
    assert (
        upload(owner, org, a["id"], statement("01.10.2026;1;x;;;")).json()["code"]
        == "bank_account_archived"
    )
    assert (
        owner.post(base(org) + f"/accounts/{a['id']}/restore", headers=h(owner)).json()["archived"]
        is False
    )
    assert {
        "bank_account.created",
        "bank_account.updated",
        "bank_account.profile_set",
        "bank_account.archived",
        "bank_account.restored",
    } <= set(actions(admin_engine))


# ---------------- предпросмотр ----------------


def test_headers_and_preview_do_not_write(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = new_account(owner, org)
    data = statement(
        "01.10.2026;1 000,00;Оплата;P-1;ООО Альфа;123 456 789", "02.10.2026;-50,00;Комиссия;;;"
    )
    hd = upload(owner, org, a["id"], data, path="headers", profile=json.dumps({"delimiter": ";"}))
    assert hd.status_code == 200 and hd.json()["headers"] == [
        "Дата",
        "Сумма",
        "Назначение",
        "Номер",
        "Контрагент",
        "ИНН",
    ]
    assert hd.json()["sample"][0]["Сумма"] == "1 000,00"
    assert (
        upload(owner, org, a["id"], data, path="preview").json()["code"] == "profile_required"
    )  # профиля нет
    broken = statement(
        "01.10.2026;1 000,00;Оплата;P-1;ООО Альфа;123456789",
        "99.99.2026;5;Плохая дата;;;",
        "03.10.2026;xyz;Плохая сумма;;;",
    )
    pv = upload(owner, org, a["id"], broken, path="preview", profile=json.dumps(PROFILE))
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert (
        body["total"],
        body["valid"],
        body["errors_count"],
        body["duplicates"],
        body["would_import"],
        body["already_imported"],
    ) == (3, 1, 2, 0, 1, False)
    assert [(e["row_number"], e["code"]) for e in body["errors"]] == [
        (3, "date_invalid"),
        (4, "amount_invalid"),
    ]
    assert (
        body["sample"][0]["amount"] == "1000.00"
        and body["sample"][0]["counterparty_tin"] == "123456789"
    )
    assert txns(owner, org) == []  # предпросмотр ничего не сохраняет
    bad_cols = upload(
        owner, org, a["id"], b"A;B\n1;2\n", path="preview", profile=json.dumps(PROFILE)
    )
    assert bad_cols.status_code == 422 and bad_cols.json()["code"] == "columns_missing"
    wrong_enc = upload(
        owner,
        org,
        a["id"],
        statement("01.10.2026;1;x;;;").decode().encode("cp1251"),
        path="preview",
        profile=json.dumps(PROFILE),
    )
    assert wrong_enc.json()["code"] in ("encoding_mismatch", "file_corrupt")


# ---------------- импорт ----------------


def test_import_is_idempotent_and_overlaps_do_not_duplicate(
    new_client: NewClient, admin_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    cp = owner.post(
        f"/api/v1/organizations/{org}/counterparties",
        json={"name": "ООО Альфа", "tin": "123456789"},
        headers=h(owner),
    ).json()["id"]
    a = with_profile(owner, org)
    first = statement(
        "01.10.2026;1 000,00;Оплата по счёту 15;P-1;ООО Альфа;123456789",
        "02.10.2026;-250,50;Комиссия банка;;;",
        "02.10.2026;-250,50;Комиссия банка;;;",
    )  # два одинаковых платежа
    r = upload(owner, org, a["id"], first)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert (imp["rows_total"], imp["rows_imported"], imp["rows_duplicate"], imp["rows_error"]) == (
        3,
        3,
        0,
        0,
    ) and imp["created_by_name"]
    items = txns(owner, org, sort="txn_date", order="asc")
    assert [(t["txn_date"], t["amount"], t["status"]) for t in items] == [
        ("2026-10-01", "1000.00", "UNMATCHED"),
        ("2026-10-02", "-250.50", "UNMATCHED"),
        ("2026-10-02", "-250.50", "UNMATCHED"),
    ]
    assert (
        items[0]["counterparty"]["id"] == cp
        and items[0]["counterparty_tin"] == "123456789"
        and items[0]["reference"] == "P-1"
    )
    # тот же файл — отказ со ссылкой на прежний импорт
    again = upload(owner, org, a["id"], first)
    assert again.status_code == 409 and again.json()["code"] == "statement_already_imported"
    assert again.json()["details"][0]["params"]["import_id"] == imp["id"]
    # перекрывающаяся выписка: старые строки (включая обе одинаковые) не дублируются, новая добавляется
    second = statement(
        "01.10.2026;1 000,00;Оплата по счёту 15;P-1;ООО Альфа;123456789",
        "02.10.2026;-250,50;Комиссия банка;;;",
        "02.10.2026;-250,50;Комиссия банка;;;",
        "03.10.2026;75,00;Возврат;;;",
    )
    r2 = upload(owner, org, a["id"], second, name="stmt2.csv")
    assert r2.status_code == 201 and (
        r2.json()["rows_total"],
        r2.json()["rows_imported"],
        r2.json()["rows_duplicate"],
    ) == (4, 1, 3)
    assert len(txns(owner, org)) == 4
    # третье такое же платёжное поручение в более новой выписке — действительно новая операция
    third = statement(
        "02.10.2026;-250,50;Комиссия банка;;;",
        "02.10.2026;-250,50;Комиссия банка;;;",
        "02.10.2026;-250,50;Комиссия банка;;;",
    )
    r3 = upload(owner, org, a["id"], third, name="stmt3.csv")
    assert r3.json()["rows_imported"] == 1 and len(txns(owner, org)) == 5
    imports = owner.get(base(org) + f"/accounts/{a['id']}/imports").json()
    assert (
        len(imports) == 3
        and owner.get(base(org) + f"/accounts/{a['id']}").json()["transactions_count"] == 5
    )
    assert "bank_import.created" in actions(admin_engine)
    # тот же файл по ДРУГОМУ счёту — разрешён (сверка идёт по счетам)
    b = with_profile(owner, org, number="40101000200300400500")
    assert upload(owner, org, b["id"], first).status_code == 201


def test_errors_block_the_file_unless_explicitly_skipped(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    data = statement(
        "01.10.2026;100,00;Хорошая;;;",
        "xx.10.2026;5;Плохая дата;;;",
        "03.10.2026;1,234;Лишние знаки;;;",
    )
    blocked = upload(owner, org, a["id"], data)
    assert blocked.status_code == 422 and blocked.json()["code"] == "statement_has_errors"
    assert [(d["code"], d["params"]["row_number"]) for d in blocked.json()["details"]] == [
        ("date_invalid", 3),
        ("amount_too_many_decimals", 4),
    ]
    assert txns(owner, org) == []
    ok = upload(owner, org, a["id"], data, skip_errors="true")
    assert ok.status_code == 201 and (ok.json()["rows_imported"], ok.json()["rows_error"]) == (1, 2)
    only_bad = upload(owner, org, a["id"], statement("xx;1;;;;"), name="bad.csv")
    assert only_bad.status_code == 422
    # валюта в файле не совпадает со счётом
    prof = dict(PROFILE, currency_column="Вал")
    owner.put(base(org) + f"/accounts/{a['id']}/profile", json=prof, headers=h(owner))
    cur = "Дата;Сумма;Назначение;Номер;Контрагент;ИНН;Вал\n01.10.2026;5;x;;;;USD\n".encode()
    r = upload(owner, org, a["id"], cur, name="cur.csv")
    assert r.status_code == 422 and r.json()["details"][0]["code"] == "currency_mismatch"


def test_split_columns_import_and_xlsx(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = new_account(owner, org)
    prof = {
        "format": "xlsx",
        "date_column": "Дата",
        "amount_mode": "split",
        "incoming_column": "Приход",
        "outgoing_column": "Расход",
        "decimal_separator": ".",
        "thousands_separator": ",",
        "description_column": "Назначение",
    }
    assert (
        owner.put(
            base(org) + f"/accounts/{a['id']}/profile", json=prof, headers=h(owner)
        ).status_code
        == 200
    )
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Дата", "Приход", "Расход", "Назначение"])
    from datetime import date

    ws.append([date(2026, 10, 1), 1500.25, None, "Поступление"])
    ws.append([date(2026, 10, 2), None, 300, "Списание"])
    buf = io.BytesIO()
    wb.save(buf)
    r = upload(owner, org, a["id"], buf.getvalue(), name="bank.xlsx")
    assert r.status_code == 201, r.text
    assert [
        (t["amount"], t["description"])
        for t in sorted(txns(owner, org), key=lambda x: x["txn_date"])
    ] == [("1500.25", "Поступление"), ("-300.00", "Списание")]


def test_dangerous_and_oversized_files_are_rejected(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    macro = io.BytesIO()
    with zipfile.ZipFile(macro, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/vbaProject.bin", b"x")
    assert (
        upload(owner, org, a["id"], macro.getvalue(), name="m.xlsx").json()["code"] == "file_unsafe"
    )
    assert (
        upload(owner, org, a["id"], b"MZ\x90\x00\x03", name="evil.csv").status_code == 422
    )  # бинарник под видом CSV
    assert upload(owner, org, a["id"], b"%PDF-1.4\n", name="s.pdf").status_code == 422
    assert upload(owner, org, a["id"], b"", name="empty.csv").json()["code"] == "file_empty"
    big = "Дата;Сумма\n".encode() + b"01.10.2026;1\n" * (5 * 1024 * 1024 // 12 + 10)
    assert upload(owner, org, a["id"], big, name="big.csv").status_code == 413
    assert txns(owner, org) == []


def test_concurrent_imports(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    from app.main import app

    a = with_profile(owner, org)
    same = statement("01.10.2026;10;a;;;", "02.10.2026;20;b;;;")
    out: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def worker() -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier.wait()
            r = c.post(
                base(org) + f"/accounts/{a['id']}/imports",
                files={"file": ("s.csv", same, "text/csv")},
                headers=hh,
            )
            with lock:
                out.append(r.status_code)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out
    assert len(txns(owner, org)) == 2
    # разные, но перекрывающиеся файлы одновременно: каждая операция попадает ровно один раз
    f1 = statement("05.10.2026;1;a;;;", "06.10.2026;2;b;;;", "07.10.2026;3;c;;;")
    f2 = statement("06.10.2026;2;b;;;", "07.10.2026;3;c;;;", "08.10.2026;4;d;;;")
    barrier2 = threading.Barrier(2)

    def worker2(data: bytes, name: str) -> None:
        with TestClient(app, cookies=dict(owner.cookies)) as c:
            hh = csrf_headers(c)
            barrier2.wait()
            c.post(
                base(org) + f"/accounts/{a['id']}/imports",
                files={"file": (name, data, "text/csv")},
                headers=hh,
            )

    ts2 = [
        threading.Thread(target=worker2, args=(f1, "f1.csv")),
        threading.Thread(target=worker2, args=(f2, "f2.csv")),
    ]
    for t in ts2:
        t.start()
    for t in ts2:
        t.join()
    assert len(txns(owner, org)) == 2 + 4  # 2 прежние + объединение {1,2,3,4} без дублей


# ---------------- список операций ----------------


def test_transactions_filters_sort_pagination(new_client: NewClient) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    upload(
        owner,
        org,
        a["id"],
        statement(
            *[
                f"{d:02d}.10.2026;{100 * (-1) ** d + d};Платёж {d};REF{d};Контрагент {d % 3};"
                for d in range(1, 11)
            ]
        ),
    )
    assert len(txns(owner, org)) == 10
    assert (
        all(float(t["amount"]) > 0 for t in txns(owner, org, direction="in"))
        and len(txns(owner, org, direction="in")) == 5
    )
    assert all(float(t["amount"]) < 0 for t in txns(owner, org, direction="out"))
    assert [
        t["txn_date"]
        for t in txns(
            owner, org, date_from="2026-10-04", date_to="2026-10-06", sort="txn_date", order="asc"
        )
    ] == ["2026-10-04", "2026-10-05", "2026-10-06"]
    assert len(txns(owner, org, q="REF7")) == 1 and len(txns(owner, org, q="платёж 1")) == 2
    assert (
        len(txns(owner, org, status="UNMATCHED")) == 10 and txns(owner, org, status="MATCHED") == []
    )
    page = owner.get(base(org) + "/transactions", params={"page": 2, "page_size": 4}).json()
    assert page["total"] == 10 and len(page["items"]) == 4
    assert owner.get(base(org) + "/transactions", params={"sort": "evil"}).status_code == 422
    other, org2 = setup_org(new_client, "two@example.uz")
    assert other.get(base(org2) + "/transactions").json()["total"] == 0
    assert other.get(base(org) + "/transactions").status_code == 404


# ---------------- неизменяемость и изоляция ----------------


def test_bank_facts_are_immutable_in_database(
    new_client: NewClient, admin_engine: Engine, app_engine: Engine
) -> None:
    owner, org = setup_org(new_client)
    a = with_profile(owner, org)
    upload(owner, org, a["id"], statement("01.10.2026;100,00;Платёж;R1;ООО;"))
    for sql in (
        "UPDATE bank_transaction SET amount = 1",
        "UPDATE bank_transaction SET description = 'x'",
        "UPDATE bank_transaction SET txn_date = '2020-01-01'",
        "DELETE FROM bank_transaction",
    ):
        with pytest.raises(DBAPIError, match=r"immutable|append-only"), admin_engine.begin() as c:
            c.execute(text(sql))
    with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as c:
        c.execute(text("DELETE FROM bank_transaction"))
    with admin_engine.begin() as c:  # статус сверки менять можно
        c.execute(text("UPDATE bank_transaction SET status = 'IGNORED'"))
    assert txns(owner, org)[0]["status"] == "IGNORED"
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(text("UPDATE bank_transaction SET status = 'WEIRD'"))
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE bank_transaction SET amount = 0 WHERE false; INSERT INTO bank_transaction (id, organization_id, bank_account_id, import_id, row_number, txn_date, amount, currency, row_hash) SELECT gen_random_uuid(), organization_id, bank_account_id, import_id, 9, txn_date, 0, 'UZS', 'zero' FROM bank_transaction"
            )
        )


def test_cross_tenant_isolation_and_rls(new_client: NewClient, app_engine: Engine) -> None:
    a_owner, org_a = setup_org(new_client, "a@example.uz")
    b_owner, org_b = setup_org(new_client, "b@example.uz")
    acc_a = with_profile(a_owner, org_a)
    acc_b = with_profile(b_owner, org_b)
    upload(a_owner, org_a, acc_a["id"], statement("01.10.2026;1;a;;;"))
    upload(b_owner, org_b, acc_b["id"], statement("01.10.2026;2;b;;;"))
    assert b_owner.get(base(org_b) + f"/accounts/{acc_a['id']}").status_code == 404
    assert upload(b_owner, org_b, acc_a["id"], statement("02.10.2026;1;x;;;")).status_code == 404
    assert b_owner.get(base(org_b) + f"/accounts/{acc_a['id']}/imports").status_code == 404
    assert (
        b_owner.get(base(org_b) + "/transactions", params={"account_id": acc_a["id"]}).json()[
            "total"
        ]
        == 0
    )
    tables = ("bank_account", "bank_import", "bank_transaction")
    with app_engine.connect() as conn:
        for t in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t
    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_a})
        for t in tables:
            assert {r[0] for r in conn.execute(text(f"SELECT organization_id::text FROM {t}"))} == {
                org_a
            }, t
