import io
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.modules.bank import parsing
from app.modules.bank.parsing import ImportProfile, ParseError, parse_amount, parse_statement


def profile(**over: object) -> ImportProfile:
    base: dict[str, object] = {
        "date_column": "Дата",
        "amount_column": "Сумма",
        "description_column": "Назначение",
    }
    base.update(over)
    return ImportProfile.model_validate(base)


def csv(text: str, enc: str = "utf-8") -> bytes:
    return text.encode(enc)


def test_amount_formats() -> None:
    p = profile()  # запятая-десятичная, пробел-тысячи
    assert parse_amount("1 250 000,50", p) == Decimal("1250000.50")
    assert parse_amount("1 250,5", p) == Decimal("1250.5")
    assert parse_amount("-100", p) == Decimal("-100")
    assert parse_amount("(100,25)", p) == Decimal("-100.25")
    assert parse_amount("+7", p) == Decimal("7")
    assert parse_amount("  ", p) is None
    dot = profile(decimal_separator=".", thousands_separator=",")
    assert parse_amount("1,250,000.50", dot) == Decimal("1250000.50")
    eu = profile(decimal_separator=",", thousands_separator=".")
    assert parse_amount("1.250.000,50", eu) == Decimal("1250000.50")
    for bad in ("1e5", "abc", "1,2,3", "12.5.5", "--5", "٣٠"):  # экспонента, мусор, не-ASCII цифры
        with pytest.raises(ValueError, match="amount_invalid"):
            parse_amount(bad, p)


def test_profile_validation() -> None:
    for over, code in (
        ({"format": "pdf"}, "profile_format_invalid"),
        ({"delimiter": "x"}, "profile_delimiter_invalid"),
        ({"encoding": "latin1"}, "profile_encoding_invalid"),
        ({"date_format": "%Y"}, "profile_date_format_invalid"),
        ({"amount_mode": "weird"}, "profile_amount_mode_invalid"),
        ({"amount_column": None}, "profile_amount_column_required"),
        ({"amount_mode": "split", "amount_column": None}, "profile_split_columns_required"),
        ({"decimal_separator": ";"}, "profile_decimal_invalid"),
        ({"decimal_separator": ",", "thousands_separator": ","}, "profile_separators_equal"),
        ({"header_row": 0}, "profile_header_row_invalid"),
    ):
        with pytest.raises(ValueError, match=code):
            profile(**over)


def test_signed_csv_normalization_and_row_errors() -> None:
    data = csv(
        "Дата;Сумма;Назначение;Контрагент;ИНН\n"
        "01.10.2026;1 000,00;Оплата по счёту 15;ООО Альфа;123 456 789\n"
        "02.10.2026;-250,5;Комиссия;;\n"
        "\n"
        "32.10.2026;5;Плохая дата;;\n"
        "03.10.2026;abc;Плохая сумма;;\n"
        "04.10.2026;0;Нулевая;;\n"
        "05.10.2026;10,123;Лишние знаки;;\n"
    )
    p = profile(counterparty_name_column="Контрагент", counterparty_tin_column="ИНН")
    res = parse_statement(data, p, currency="UZS", minor_units=2)
    assert res.total == 6  # пустая строка не считается
    assert [(r.row_number, str(r.amount), r.counterparty_tin) for r in res.rows] == [
        (2, "1000.00", "123456789"),
        (3, "-250.5", None),
    ]
    assert [(e.row_number, e.code) for e in res.errors] == [
        (5, "date_invalid"),
        (6, "amount_invalid"),
        (7, "amount_zero"),
        (8, "amount_too_many_decimals"),
    ]
    assert (
        res.rows[0].description == "Оплата по счёту 15"
        and res.rows[0].counterparty_name == "ООО Альфа"
    )


def test_split_columns_and_currency_check() -> None:
    data = csv(
        "Дата;Приход;Расход;Вал\n01.10.2026;100,00;;UZS\n02.10.2026;;40,00;UZS\n03.10.2026;5;5;UZS\n04.10.2026;;;UZS\n05.10.2026;1;;USD\n06.10.2026;-1;;UZS\n"
    )
    p = profile(
        amount_mode="split",
        amount_column=None,
        incoming_column="Приход",
        outgoing_column="Расход",
        currency_column="Вал",
        description_column=None,
    )
    res = parse_statement(data, p, currency="UZS", minor_units=2)
    assert [(r.row_number, str(r.amount)) for r in res.rows] == [(2, "100.00"), (3, "-40.00")]
    assert [(e.row_number, e.code) for e in res.errors] == [
        (4, "amount_both_sides"),
        (5, "amount_missing"),
        (6, "currency_mismatch"),
        (7, "amount_negative_in_split"),
    ]


def test_identical_rows_get_distinct_hashes_and_overlaps_get_equal_hashes() -> None:
    p = profile()
    one = parse_statement(
        csv("Дата;Сумма;Назначение\n01.10.2026;100;Взнос\n01.10.2026;100;Взнос\n"),
        p,
        currency="UZS",
        minor_units=2,
    )
    assert len({r.row_hash for r in one.rows}) == 2  # два одинаковых платежа — две операции
    two = parse_statement(
        csv(
            "Дата;Сумма;Назначение\n01.10.2026;100;Взнос\n01.10.2026;100;Взнос\n02.10.2026;5;Новая\n"
        ),
        p,
        currency="UZS",
        minor_units=2,
    )
    assert {r.row_hash for r in one.rows} <= {
        r.row_hash for r in two.rows
    }  # перекрывающаяся выписка узнаёт старые строки


def test_header_row_encoding_and_missing_columns() -> None:
    p = profile(header_row=3, encoding="cp1251")
    data = csv("Банк;Выписка\nпериод;октябрь\nДата;Сумма;Назначение\n01.10.2026;1;ок\n", "cp1251")
    assert len(parse_statement(data, p, currency="UZS", minor_units=2).rows) == 1
    with pytest.raises(ParseError, match="кодировке"):
        parse_statement(
            csv("Дата;Сумма\n01.10.2026;1\n", "cp1251"), profile(), currency="UZS", minor_units=2
        )
    with pytest.raises(ParseError) as exc:
        parse_statement(
            csv("Дата;Другая\n01.10.2026;1\n"), profile(), currency="UZS", minor_units=2
        )
    assert exc.value.code == "columns_missing" and "Сумма" in exc.value.message
    with pytest.raises(ParseError) as exc2:
        parse_statement(csv("a;b\n"), profile(header_row=5), currency="UZS", minor_units=2)
    assert exc2.value.code == "header_not_found"


def test_xlsx_dates_numbers_and_sheet() -> None:
    from datetime import date

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Выписка"
    ws.append(["Дата", "Сумма", "Назначение"])
    ws.append([date(2026, 10, 1), 1250.5, "Оплата"])
    ws.append(["02.10.2026", "-100,00", "Текстовая дата"])
    ws.append([date(2026, 10, 3), 0.1 + 0.2, "Артефакт float"])
    other = wb.create_sheet("Другой")
    other.append(["x"])
    buf = io.BytesIO()
    wb.save(buf)
    p = profile(format="xlsx", sheet="Выписка", decimal_separator=".")
    res = parse_statement(buf.getvalue(), p, currency="UZS", minor_units=2)
    # ячейки-даты приходят датами; число — точным десятичным; «-100,00» при точке-разделителе — ошибка, а не догадка
    assert [(r.row_number, str(r.txn_date), str(r.amount)) for r in res.rows] == [
        (2, "2026-10-01", "1250.5"),
        (4, "2026-10-03", "0.3"),
    ]
    assert [(e.row_number, e.code) for e in res.errors] == [(3, "amount_invalid")]
    with pytest.raises(ParseError, match="Лист"):
        parse_statement(
            buf.getvalue(), profile(format="xlsx", sheet="Нет"), currency="UZS", minor_units=2
        )


def test_limits() -> None:
    body = "Дата;Сумма\n" + "".join(f"01.10.2026;{i + 1}\n" for i in range(parsing.MAX_ROWS + 1))
    with pytest.raises(ParseError) as exc:
        parse_statement(csv(body), profile(), currency="UZS", minor_units=2)
    assert exc.value.code == "too_many_rows"
