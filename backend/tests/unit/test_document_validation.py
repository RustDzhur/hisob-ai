from datetime import date
from decimal import Decimal as D

from app.modules.documents.validation import HeaderData, LineData, tolerance, validate

TODAY = date(2026, 10, 10)


def header(**kw: object) -> HeaderData:
    base: dict[str, object] = dict(
        doc_type="invoice",
        number="1",
        doc_date=date(2026, 10, 1),
        has_counterparty=True,
        currency="UZS",
        total_amount=D("200.00"),
        tax_total=None,
    )
    base.update(kw)
    return HeaderData(**base)  # type: ignore[arg-type]


def line(pos: int = 1, **kw: object) -> LineData:
    base: dict[str, object] = dict(quantity=D(2), unit_price=D("100"), amount=D("200"))
    base.update(kw)
    return LineData(position=pos, **base)  # type: ignore[arg-type]


def codes(issues: list) -> set[tuple[str, str]]:  # type: ignore[type-arg]
    return {(i.code, i.severity) for i in issues}


def test_clean_document_has_no_issues() -> None:
    assert validate(header(), [line()], minor_units=2, today=TODAY) == []


def test_required_fields_are_errors() -> None:
    issues = validate(
        header(
            number=None, doc_date=None, has_counterparty=False, total_amount=None, currency=None
        ),
        [],
        minor_units=None,
        today=TODAY,
    )
    assert codes(issues) == {
        ("date_required", "error"),
        ("number_required", "error"),
        ("counterparty_required", "error"),
        ("total_required", "error"),
        ("lines_required", "error"),
    }


def test_type_specific_requirements() -> None:
    contract = validate(
        header(doc_type="contract", total_amount=None), [], minor_units=2, today=TODAY
    )
    assert contract == []  # у договора нет строк и обязательной суммы
    receipt = validate(
        header(doc_type="receipt", number=None, has_counterparty=False),
        [line()],
        minor_units=2,
        today=TODAY,
    )
    assert receipt == []
    stmt = validate(
        header(doc_type="statement", has_counterparty=False), [], minor_units=2, today=TODAY
    )
    assert ("counterparty_required", "error") not in codes(stmt)


def test_currency_required_when_money_present() -> None:
    assert ("currency_required", "error") in codes(
        validate(header(currency=None), [line()], minor_units=None, today=TODAY)
    )
    assert (
        validate(
            header(doc_type="contract", total_amount=None, currency=None),
            [],
            minor_units=None,
            today=TODAY,
        )
        == []
    )


def test_future_date_is_warning() -> None:
    assert ("date_in_future", "warning") in codes(
        validate(header(doc_date=date(2026, 10, 20)), [line()], minor_units=2, today=TODAY)
    )
    assert (
        validate(header(doc_date=date(2026, 10, 11)), [line()], minor_units=2, today=TODAY) == []
    )  # завтра допустимо


def test_line_amount_mismatch_and_tolerance() -> None:
    bad = validate(
        header(total_amount=D("250")), [line(amount=D("250"))], minor_units=2, today=TODAY
    )
    mm = [i for i in bad if i.code == "line_amount_mismatch"]
    assert len(mm) == 1 and mm[0].params == {
        "position": 1,
        "expected": "200.00",
        "actual": "250.00",
    }
    # разница в одну минимальную единицу — в пределах допуска
    ok = validate(
        header(total_amount=D("200.01")), [line(amount=D("200.01"))], minor_units=2, today=TODAY
    )
    assert ok == []
    # скидка учитывается
    disc = validate(
        header(total_amount=D("180")),
        [line(discount=D("20"), amount=D("180"))],
        minor_units=2,
        today=TODAY,
    )
    assert disc == []


def test_total_matches_net_or_gross_but_not_neither() -> None:
    lines = [line(1, tax_amount=D("24")), line(2, tax_amount=D("24"))]
    net = D("400")
    for total in (net, net + D("48")):  # цены без налога или с налогом — обе схемы допустимы
        assert (
            validate(
                header(total_amount=total, tax_total=D("48")), lines, minor_units=2, today=TODAY
            )
            == []
        )
    wrong = validate(
        header(total_amount=D("999"), tax_total=D("48")), lines, minor_units=2, today=TODAY
    )
    assert ("total_mismatch", "warning") in codes(wrong)
    tax = validate(header(total_amount=net, tax_total=D("40")), lines, minor_units=2, today=TODAY)
    assert ("tax_total_mismatch", "warning") in codes(tax)


def test_incomplete_line_is_warning_and_skips_total_check() -> None:
    issues = validate(
        header(total_amount=D("999")), [line(1), LineData(position=2)], minor_units=2, today=TODAY
    )
    assert ("line_incomplete", "warning") in codes(issues)
    assert ("total_mismatch", "warning") not in codes(issues)


def test_tolerance_grows_with_number_of_terms() -> None:
    assert tolerance(2, 1) == D("0.01")
    assert tolerance(2, 10) == D("0.05")
    assert tolerance(0, 1) == D(1)
