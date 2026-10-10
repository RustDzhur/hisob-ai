"""Исполнитель задачи распознавания (выполняется в воркере, вне запроса пользователя)."""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.documents.extraction import apply, inputs
from app.modules.documents.extraction.providers import (
    ProviderError,
    get_fallback_provider,
    get_provider,
)
from app.modules.documents.extraction.schema import ExtractionOut
from app.modules.documents.models import (
    AIJob,
    AIResult,
    AIRuntime,
    Document,
    DocumentFile,
    DocumentLine,
)
from app.platform.base import utcnow
from app.platform.db import get_sessionmaker, set_context
from app.platform.storage import get_storage

log = logging.getLogger("app.extraction")


class RetryLater(Exception):
    """Временная ошибка: очередь повторит задачу позже."""


def _doc_in_work(doc: Document) -> bool:
    return doc.status in ("QUEUED", "EXTRACTING")


def _fail(db: Session, job: AIJob, doc: Document | None, code: str, message: str) -> None:
    job.status, job.error_code, job.error_message, job.finished_at = (
        "failed",
        code,
        message[:300],
        utcnow(),
    )
    if doc is not None and _doc_in_work(doc):
        doc.status = "NEEDS_REVIEW"
        doc.version += 1
    audit.record(
        db,
        action="document.extraction_failed",
        actor_user_id=None,
        organization_id=job.organization_id,
        object_type="document",
        object_id=job.document_id,
        after={
            "job_id": str(job.id),
            "provider": job.provider,
            "model": job.model,
            "error_code": code,
        },
    )
    db.commit()


# Коды отказа основного провайдера, при которых пробуем запасной. Ошибки файла и схемы не в списке.
FALLBACK_CODES = frozenset(
    {
        "provider_not_configured",
        "provider_auth",
        "provider_model_unavailable",
        "provider_bad_request",
        "provider_rate_limited",
        "provider_unavailable",
        "provider_timeout",
        "provider_invalid_response",
        "provider_empty_output",
        "provider_incomplete",
        "provider_refusal",
        "invalid_provider_output",
    }
)


def run_job(job_id: uuid.UUID, org_id: uuid.UUID, *, final_attempt: bool = True) -> None:
    """Идемпотентно: повторный вызов для завершённой/отменённой задачи ничего не делает."""
    with get_sessionmaker()() as db:
        set_context(db, org_id=org_id)
        job = db.scalar(select(AIJob).where(AIJob.id == job_id).with_for_update())
        if job is None or job.status not in ("queued", "running"):
            return
        doc = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        if doc is None:
            return
        f = db.scalar(select(DocumentFile).where(DocumentFile.document_id == doc.id))
        if f is None:
            _fail(db, job, doc, "file_missing", "У документа нет файла.")
            return
        job.status = "running"
        job.attempts += 1
        job.started_at = job.started_at or utcnow()
        if doc.status == "QUEUED":
            doc.status = "EXTRACTING"
            doc.version += 1
        db.commit()
        storage_key, content_type, name = f.storage_key, f.content_type, f.original_name
        provider_name, job_id_s = job.provider, str(job.id)

    # --- вызов провайдера: без открытых транзакций БД ---
    error: tuple[str, str, bool] | None = None
    result = None
    used: tuple[str, str, str, str] | None = None  # (запасной, модель, основной, код отказа)
    try:
        data = b"".join(get_storage().stream(storage_key))
        inp = inputs.build_input(content_type, data, name)
        provider = get_provider()
        if provider.name != provider_name:
            raise ProviderError("provider_changed", "Провайдер изменился после постановки задачи.")
        try:
            result = provider.extract(inp)
        except ProviderError as primary_exc:
            fb = get_fallback_provider(provider)
            if fb is None or primary_exc.code not in FALLBACK_CODES:
                raise
            log.warning(
                "primary provider failed job=%s code=%s, trying %s",
                job_id_s,
                primary_exc.code,
                fb.name,
            )
            try:
                result = fb.extract(inp)
            except ProviderError as fb_exc:
                log.warning("fallback failed job=%s code=%s", job_id_s, fb_exc.code)
                if fb_exc.code == "provider_unsupported_input":
                    raise primary_exc from None
                raise ProviderError(
                    primary_exc.code,
                    f"{primary_exc.message} Запасной провайдер ({fb.name}): {fb_exc.code}.",
                    retryable=primary_exc.retryable or fb_exc.retryable,
                ) from None
            used = (fb.name, fb.model, provider.name, primary_exc.code)
    except inputs.InputError as exc:
        error = (
            str(exc) or "input_unreadable",
            "Не удалось подготовить файл для распознавания.",
            False,
        )
    except ProviderError as exc:
        error = (exc.code, exc.message, exc.retryable)
    except Exception as exc:
        log.warning("extraction infrastructure error job=%s type=%s", job_id_s, type(exc).__name__)
        error = ("infrastructure_error", "Временная ошибка инфраструктуры.", True)

    with get_sessionmaker()() as db:
        set_context(db, org_id=org_id)
        job = db.scalar(select(AIJob).where(AIJob.id == job_id).with_for_update())
        if job is None or job.status != "running":
            return  # отменена, пока шёл запрос: результат отбрасываем
        doc = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())

        if error is not None:
            code, message, retryable = error
            if retryable and not final_attempt:
                db.commit()
                raise RetryLater(code)
            _fail(db, job, doc, code, message)
            return
        assert result is not None and doc is not None

        try:
            out = ExtractionOut.model_validate(result.payload)
        except Exception:
            _fail(
                db, job, doc, "invalid_provider_output", "Ответ провайдера не соответствует схеме."
            )
            return

        existing = (
            db.scalar(
                select(func.count())
                .select_from(DocumentLine)
                .where(DocumentLine.document_id == doc.id)
            )
            or 0
        )
        summary, matches = apply.apply_extraction(db, org_id, doc, job, out, int(existing))
        db.add(
            AIResult(
                job_id=job.id, organization_id=org_id, raw_output=result.payload, matches=matches
            )
        )
        if used is not None:
            job.provider, job.model = used[0], used[1]
        job.status, job.finished_at = "succeeded", utcnow()
        job.tokens_in, job.tokens_out = result.tokens_in, result.tokens_out
        if _doc_in_work(doc):
            doc.status = "NEEDS_REVIEW"
        doc.version += 1
        audit.record(
            db,
            action="document.extracted",
            actor_user_id=None,
            organization_id=org_id,
            object_type="document",
            object_id=doc.id,
            after={
                "job_id": str(job.id),
                "provider": job.provider,
                "model": job.model,
                "applied_fields": summary.applied,
                "suggested_fields": summary.suggested,
                "lines_created": summary.lines_created,
                **({"fallback_from": used[2], "primary_error": used[3]} if used else {}),
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
            },
        )
        db.commit()


def update_runtime() -> dict[str, Any]:
    """Воркер сообщает API, какой провайдер настроен (без ключей)."""
    from app.platform.config import get_settings

    s = get_settings()
    try:
        p = get_provider()
        provider, model, configured = p.name, p.model, p.configured()
    except ProviderError:
        provider, model, configured = s.ai_provider, "", False
    with get_sessionmaker()() as db:
        row = db.get(AIRuntime, 1)
        now: datetime = utcnow()
        if row is None:
            db.add(
                AIRuntime(
                    id=1, provider=provider, model=model, configured=configured, updated_at=now
                )
            )
        else:
            row.provider, row.model, row.configured, row.updated_at = (
                provider,
                model,
                configured,
                now,
            )
        db.commit()
    return {"provider": provider, "model": model, "configured": configured}
