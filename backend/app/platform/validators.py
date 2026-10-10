import re

_TIN_RE = re.compile(r"^\d{9,14}$")


def validate_tin(v: str | None) -> str | None:
    """ИНН/СТИР: предварительная проверка формата (только цифры, 9–14 знаков).

    Точные правила подтверждает бухгалтер (Q9); пустое значение допустимо.
    """
    if v is None or v.strip() == "":
        return None
    v = v.strip()
    if not _TIN_RE.match(v):
        raise ValueError("tin_invalid")
    return v
