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
