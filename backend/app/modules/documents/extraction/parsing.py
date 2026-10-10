"""Разбор значений, которые вернул AI: строго и без «угадывания». Неразобранное -> None."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d", "%d-%m-%Y")
_NUM_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")  # только ASCII-цифры


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    v = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(v, fmt).date()
        except ValueError:
            continue
        return d if 2000 <= d.year <= 2100 else None
    return None


def parse_decimal(value: str | None, *, max_decimals: int | None = None) -> Decimal | None:
    """«1 250 000,50» и «1250000.5» -> Decimal; все остальное (в т.ч. float-подобное «1e5») -> None."""
    if not value:
        return None
    v = value.replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in v and "." in v:  # «1.250,50» / «1,250.50»: разделитель разрядов — тот, что левее
        v = (
            v.replace(".", "").replace(",", ".")
            if v.rfind(",") > v.rfind(".")
            else v.replace(",", "")
        )
    else:
        v = v.replace(",", ".")
    if not _NUM_RE.match(v):
        return None
    try:
        d = Decimal(v)
    except InvalidOperation:
        return None
    if max_decimals is not None and -d.normalize().as_tuple().exponent > max_decimals:  # type: ignore[operator]
        return None
    return d


def digits_only(value: str | None) -> str | None:
    if not value:
        return None
    d = re.sub(r"[^0-9]", "", value)
    return d or None


_LEGAL_FORMS = re.compile(
    r"\b(ооо|ао|оао|зао|ип|чп|мчж|ояк|аж|xk|llc|ltd|jsc|ooo|mchj|yatt|ytt|fx|qk)\b", re.IGNORECASE
)


def normalize_name(value: str | None) -> str:
    """Для сопоставления: без организационной формы, кавычек и пунктуации, в нижнем регистре."""
    if not value:
        return ""
    v = _LEGAL_FORMS.sub(" ", value.casefold())
    v = re.sub(r"[^\w\s]", " ", v, flags=re.UNICODE)
    return re.sub(r"\s+", " ", v).strip()


def tokens(value: str | None) -> set[str]:
    return {t for t in normalize_name(value).split() if len(t) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
