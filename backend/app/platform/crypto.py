"""Шифрование чувствительных значений в БД (секреты TOTP): Fernet (AES-128-CBC + HMAC-SHA256) от APP_SECRET_KEY.

Ключ — из окружения, в БД не хранится. Смена ключа делает старые значения нечитаемыми (потребуется повторная
настройка второго фактора). Пустой ключ — шифрование недоступно, функции, которым оно нужно, отключены.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.platform.config import get_settings


class CryptoUnavailable(Exception):
    pass


def _fernet() -> Fernet:
    secret = get_settings().app_secret_key
    if len(secret) < 16:
        raise CryptoUnavailable("APP_SECRET_KEY не задан или слишком короткий")
    key = base64.urlsafe_b64encode(hashlib.sha256(b"hisob-ai/v1:" + secret.encode()).digest())
    return Fernet(key)


def available() -> bool:
    return len(get_settings().app_secret_key) >= 16


def encrypt(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt(token: bytes) -> str | None:
    try:
        return _fernet().decrypt(token).decode()
    except InvalidToken:
        return None
