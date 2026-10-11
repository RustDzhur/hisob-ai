"""TOTP по RFC 6238 (HMAC-SHA1, 30 секунд, 6 цифр) без внешних библиотек; проверено на векторах RFC."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6
WINDOW = 1  # допускаем соседние интервалы (расхождение часов)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def hotp(secret: str, counter: int) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return str(code).zfill(DIGITS)


def at(secret: str, ts: float | None = None) -> str:
    return hotp(secret, int((time.time() if ts is None else ts) // STEP))


def verify(secret: str, code: str, last_step: int | None, ts: float | None = None) -> int | None:
    """Возвращает номер интервала принятого кода или None. Код одного интервала принимается один раз."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    now = int((time.time() if ts is None else ts) // STEP)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"
    )
