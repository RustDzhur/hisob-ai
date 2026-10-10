import pytest

from app.modules.identity.policy import validate_password
from app.platform.errors import AppError
from app.platform.idempotency import request_hash
from app.platform.security import (
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)


def test_password_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("s3cret-password")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "s3cret-password")
    assert not verify_password(h, "other")
    assert not password_needs_rehash(h)


def test_verify_password_garbage_hash_is_false() -> None:
    assert verify_password("not-a-hash", "x") is False


def test_tokens_are_random_and_hashed() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43
    assert hash_token(a) != hash_token(b)
    assert len(hash_token(a)) == 32
    assert a.encode() not in hash_token(a)


@pytest.mark.parametrize(
    "password,email",
    [("short", "a@b.uz"), ("x" * 129, "a@b.uz"), ("same@b.uz", "same@b.uz")],
)
def test_weak_passwords_rejected(password: str, email: str) -> None:
    with pytest.raises(AppError) as exc:
        validate_password(password, email=email, min_length=10)
    assert exc.value.code == "weak_password"


def test_request_hash_is_order_independent() -> None:
    assert request_hash({"a": 1, "b": 2}) == request_hash({"b": 2, "a": 1})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


def test_plain_decimal_validator_rejects_exponent_and_floats() -> None:
    import pytest

    from app.platform.validators import reject_non_plain_number

    for ok in ("1", "1.5", "-2", " 3.25 ", 7):
        assert reject_non_plain_number(ok, "x") == ok
    for bad in ("1e3", "1E3", "+1", "Infinity", "NaN", ".5", "5.", 1.5):
        with pytest.raises(ValueError, match="x"):
            reject_non_plain_number(bad, "x")
    for garbage in ("abc", "1,5", "", "0x10"):  # не числа: дальше решает pydantic
        assert reject_non_plain_number(garbage, "x") == garbage
