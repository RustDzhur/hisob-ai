import re
from decimal import Decimal, InvalidOperation

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


# Обычная длина налогового номера: 9 знаков (СТИР/ИНН) или 14 (ПИНФЛ). Предварительное правило (Q9):
# используется только для предупреждений и подсказок, но не блокирует ввод (контрагент может быть иностранным).
USUAL_TIN_LENGTHS = (9, 14)


def tin_length_usual(v: str | None) -> bool:
    return v is not None and len(v) in USUAL_TIN_LENGTHS


# Кириллические буквы, неотличимые от латинских: «ТН-88» и «TH-88» — один и тот же номер на глаз.
_CONFUSABLES = str.maketrans("авекмнорстух", "abekmhopctyx")
CONFUSABLE_FROM = "авекмнорстух"
CONFUSABLE_TO = "abekmhopctyx"


def fold_confusables(v: str) -> str:
    """Для СРАВНЕНИЯ номеров: нижний регистр, пробелы по краям убраны, кириллические двойники -> латиница."""
    return v.strip().lower().translate(_CONFUSABLES)


_PLAIN_DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")


def reject_non_plain_number(v: object, code: str) -> object:
    """Числа в API — обычные десятичные записи: без float и без форм, которые Decimal понимает, а бухгалтер нет
    («1e3», «+1», «.5», «Infinity»). Мусор вроде «abc» пропускается дальше: его разберёт pydantic.

    Целые (int) допустимы; значение проверяется до разбора Decimal.
    """
    if isinstance(v, float):
        raise ValueError(code)
    if isinstance(v, str) and not _PLAIN_DECIMAL.match(v.strip()):
        try:
            Decimal(v.strip())
        except InvalidOperation:
            return v
        raise ValueError(code)
    return v
