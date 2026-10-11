"""Контракт адаптера интеграции и реестр провайдеров (этап 7).

Официальной документации и тестовых доступов Multibank / Faktura.uz / Didox нет (Q12), поэтому для них
адаптеров нет: статус NOT_CONFIGURED, возможности пусты. Адреса, поля и права доступа не придумываются.
Будущий адаптер обязан реализовать Adapter и описать: auth, capabilities, pagination, rate limits,
retries, webhooks, sync cursor, error mapping (ТЗ §11) — только по официальной документации.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

Status = Literal["AVAILABLE", "MOCK", "NOT_CONFIGURED"]
Kind = Literal["generic_file", "external", "mock"]


@dataclass(frozen=True)
class ProviderInfo:
    code: str
    title: str
    kind: Kind
    status: Status
    docs_received: bool
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class SyncResult:
    records: int
    next_cursor: str | None


class AdapterError(Exception):
    """Ошибка адаптера с кодом из закрытого списка (error mapping)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Adapter(Protocol):
    info: ProviderInfo

    def sync(self, cursor: str | None) -> SyncResult: ...


class MockAdapter:
    """Тестовый адаптер: детерминированные «записи» без обращения к сети и без записи в учёт."""

    info = ProviderInfo(
        code="mock",
        title="Тестовый адаптер",
        kind="mock",
        status="MOCK",
        docs_received=False,
        capabilities=("sync_demo",),
    )
    PAGE = 3
    LIMIT = 9  # после девятой записи новых данных нет

    def sync(self, cursor: str | None) -> SyncResult:
        pos = int(cursor) if cursor else 0
        if pos < 0 or pos > self.LIMIT:
            raise AdapterError("bad_cursor")
        n = max(0, min(self.PAGE, self.LIMIT - pos))
        return SyncResult(records=n, next_cursor=str(pos + n))


PROVIDERS: dict[str, ProviderInfo] = {
    "multibank": ProviderInfo("multibank", "Multibank", "external", "NOT_CONFIGURED", False, ()),
    "faktura_uz": ProviderInfo("faktura_uz", "Faktura.uz", "external", "NOT_CONFIGURED", False, ()),
    "didox": ProviderInfo("didox", "Didox", "external", "NOT_CONFIGURED", False, ()),
    "generic_file": ProviderInfo(
        "generic_file",
        "Файлы (CSV/XLSX, PDF)",
        "generic_file",
        "AVAILABLE",
        True,
        ("bank_statement_import", "document_upload"),
    ),
    "mock": MockAdapter.info,
}
ADAPTERS: dict[str, Adapter] = {"mock": MockAdapter()}
