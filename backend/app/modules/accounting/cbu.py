"""Официальные курсы Центрального банка Узбекистана (cbu.uz).

Формат проверен по живому ответу: GET https://cbu.uz/ru/arkhiv-kursov-valyut/json/all/YYYY-MM-DD/ возвращает
список объектов {"Ccy": "USD", "Nominal": "1", "Rate": "11846.57", "Date": "09.10.2026", ...}; курс — сумов за
Nominal единиц валюты. Если на запрошенную дату курсов нет, сервис возвращает ближайшие опубликованные (поле Date
другое) — такой ответ мы НЕ принимаем, чтобы не записать курс на неверную дату.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

CBU_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/all/{d}/"


class CbuError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CbuRate:
    currency: str
    nominal: int
    rate: Decimal


_client_factory: Callable[[], httpx.Client] | None = None


def set_client_for_tests(factory: Callable[[], httpx.Client] | None) -> None:
    global _client_factory
    _client_factory = factory


def source_ref(d: date) -> str:
    return CBU_URL.format(d=d.isoformat())


def fetch_rates(d: date) -> list[CbuRate]:
    client = (
        _client_factory() if _client_factory else httpx.Client(timeout=15, follow_redirects=False)
    )
    try:
        resp = client.get(source_ref(d), headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CbuError("rates_provider_unavailable", "Сервис курсов ЦБ недоступен.") from exc
    finally:
        client.close()
    if resp.status_code != 200:
        raise CbuError(
            "rates_provider_unavailable", f"Сервис курсов ЦБ ответил кодом {resp.status_code}."
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise CbuError("rates_provider_invalid", "Ответ сервиса курсов ЦБ не разобран.") from exc
    if not isinstance(data, list) or not data:
        raise CbuError("rates_provider_invalid", "Ответ сервиса курсов ЦБ имеет неожиданный вид.")
    out: list[CbuRate] = []
    for item in data:
        try:
            if datetime.strptime(str(item["Date"]), "%d.%m.%Y").date() != d:
                raise CbuError("rates_not_published", "На эту дату курсы ЦБ не опубликованы.")
            ccy = str(item["Ccy"]).upper()
            nominal = int(str(item["Nominal"]))
            rate = Decimal(str(item["Rate"]))
        except CbuError:
            raise
        except (KeyError, ValueError, InvalidOperation, TypeError) as exc:
            raise CbuError(
                "rates_provider_invalid", "Ответ сервиса курсов ЦБ имеет неожиданный вид."
            ) from exc
        if len(ccy) != 3 or nominal <= 0 or rate <= 0:
            continue
        out.append(CbuRate(ccy, nominal, rate))
    return out
