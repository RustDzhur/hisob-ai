import pytest

from app.modules.audit.service import _clean


def test_secret_keys_are_refused() -> None:
    for key in ("password", "Password_Hash", "token", "secret"):
        with pytest.raises(ValueError):
            _clean({key: "x"})


def test_regular_payload_passes() -> None:
    assert _clean({"email": "a@b.uz"}) == {"email": "a@b.uz"}
    assert _clean(None) is None
