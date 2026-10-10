"""Воркер фоновых задач (Dramatiq + Redis). Запуск: dramatiq app.worker --processes 1 --threads 2

Без REDIS_URL (тесты) используется заглушка брокера: задачи не выполняются сами, тесты вызывают runner напрямую.
"""

import logging
import uuid

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import CurrentMessage, Middleware

from app import registry  # noqa: F401  (все модели нужны воркеру для внешних ключей)
from app.modules.documents.extraction import runner
from app.platform.config import get_settings
from app.platform.logging import configure_logging

MAX_RETRIES = 3
log = logging.getLogger("app.worker")


class RuntimeStatusMiddleware(Middleware):
    """При старте воркер записывает в БД, какой провайдер настроен (ключей там нет)."""

    def after_worker_boot(self, broker: dramatiq.Broker, worker: dramatiq.Worker) -> None:
        configure_logging()
        try:
            info = runner.update_runtime()
            log.info(
                "worker ready provider=%s model=%s configured=%s",
                info["provider"],
                info["model"],
                info["configured"],
            )
        except Exception:
            log.exception("could not publish runtime status")


_settings = get_settings()
broker: dramatiq.Broker = (
    RedisBroker(url=_settings.redis_url) if _settings.redis_url else StubBroker()
)
broker.add_middleware(CurrentMessage())
broker.add_middleware(RuntimeStatusMiddleware())
dramatiq.set_broker(broker)


@dramatiq.actor(
    queue_name="extraction",
    max_retries=MAX_RETRIES,
    min_backoff=10_000,
    max_backoff=120_000,
    time_limit=300_000,
)
def extract_document(job_id: str, org_id: str) -> None:
    message = CurrentMessage.get_current_message()
    retries = int(message.options.get("retries", 0)) if message else 0
    runner.run_job(uuid.UUID(job_id), uuid.UUID(org_id), final_attempt=retries >= MAX_RETRIES)
