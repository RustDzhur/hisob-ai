"""Use cases распознавания на стороне API: запуск, отмена, статус. Саму работу выполняет воркер."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.documents import service as docs
from app.modules.documents.extraction.schema import PROMPT_VERSION
from app.modules.documents.models import AIJob, AIRuntime, Document, DocumentFile
from app.modules.documents.schemas import AIStatusOut, DocumentOut
from app.modules.identity.service import Principal
from app.modules.rbac import service as rbac
from app.modules.tenancy.access import authorize
from app.platform.base import new_id, utcnow
from app.platform.config import get_settings
from app.platform.errors import AppError

log = logging.getLogger("app.extraction")


def _fail(http: int, code: str, message: str, **params: object) -> AppError:
    return AppError(
        http, code, message, [{"field": "", "code": code, "message": message, "params": params}]
    )


def ai_status(db: Session, principal: Principal, org_id: uuid.UUID) -> AIStatusOut:
    authorize(db, principal, org_id, rbac.DOCUMENTS_READ)
    rt = db.get(AIRuntime, 1)
    if rt is None:
        return AIStatusOut(configured=False, provider=None, model=None, mock=False)
    return AIStatusOut(
        configured=rt.configured, provider=rt.provider, model=rt.model, mock=rt.provider == "mock"
    )


def start_extraction(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID
) -> DocumentOut:
    access = authorize(db, principal, org_id, rbac.DOCUMENTS_REVIEW)
    doc = db.scalar(
        select(Document)
        .where(Document.id == doc_id, Document.organization_id == org_id)
        .with_for_update()
    )
    if doc is None:
        raise AppError(404, "not_found", "Документ не найден.")
    if doc.status != "NEEDS_REVIEW":
        raise _fail(
            409,
            "invalid_status_transition",
            "Распознавание доступно только для документа на проверке.",
            status=doc.status,
        )
    if db.scalar(select(DocumentFile.id).where(DocumentFile.document_id == doc.id)) is None:
        raise AppError(404, "not_found", "У документа нет файла.")
    rt = db.get(AIRuntime, 1)
    if rt is None or not rt.configured:
        raise _fail(
            409, "ai_not_configured", "Распознавание не настроено: не заданы провайдер и ключ."
        )
    limit = get_settings().ai_max_jobs_per_day
    since = utcnow() - timedelta(days=1)
    used = (
        db.scalar(
            select(func.count())
            .select_from(AIJob)
            .where(AIJob.organization_id == org_id, AIJob.created_at >= since)
        )
        or 0
    )
    if used >= limit:
        raise _fail(429, "ai_quota_exceeded", "Суточный лимит распознаваний исчерпан.", limit=limit)

    job = AIJob(
        id=new_id(),
        organization_id=org_id,
        document_id=doc.id,
        status="queued",
        provider=rt.provider,
        model=rt.model,
        prompt_version=PROMPT_VERSION,
        requested_by=principal.user.id,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError as exc:  # уже есть активная задача
        db.rollback()
        raise _fail(409, "extraction_in_progress", "Для документа уже идёт распознавание.") from exc
    doc.status = "QUEUED"
    doc.version += 1
    audit.record(
        db,
        action="document.extraction_requested",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="document",
        object_id=doc.id,
        after={"job_id": str(job.id), "provider": job.provider, "model": job.model},
    )
    db.commit()

    from app.worker import extract_document  # отложенный импорт: воркер тянет модули приложения

    try:
        extract_document.send(str(job.id), str(org_id))
    except Exception as exc:
        log.warning("enqueue failed job=%s type=%s", job.id, type(exc).__name__)
        job.status, job.error_code, job.finished_at = "failed", "queue_unavailable", utcnow()
        doc.status = "NEEDS_REVIEW"
        doc.version += 1
        db.commit()
        raise _fail(503, "queue_unavailable", "Очередь недоступна. Повторите позже.") from exc
    db.refresh(doc)
    return docs._load_out(db, access, doc)


def cancel_extraction(
    db: Session, principal: Principal, org_id: uuid.UUID, doc_id: uuid.UUID
) -> DocumentOut:
    access = authorize(db, principal, org_id, rbac.DOCUMENTS_REVIEW)
    doc = db.scalar(
        select(Document)
        .where(Document.id == doc_id, Document.organization_id == org_id)
        .with_for_update()
    )
    if doc is None:
        raise AppError(404, "not_found", "Документ не найден.")
    job = db.scalar(
        select(AIJob)
        .where(AIJob.document_id == doc.id, AIJob.status.in_(("queued", "running")))
        .with_for_update()
    )
    if job is None:
        raise _fail(
            409, "invalid_status_transition", "Активного распознавания нет.", status=doc.status
        )
    job.status, job.finished_at = "cancelled", utcnow()
    if doc.status in ("QUEUED", "EXTRACTING"):
        doc.status = "NEEDS_REVIEW"
        doc.version += 1
    audit.record(
        db,
        action="document.extraction_cancelled",
        actor_user_id=principal.user.id,
        organization_id=org_id,
        object_type="document",
        object_id=doc.id,
        after={"job_id": str(job.id)},
    )
    db.commit()
    db.refresh(doc)
    return docs._load_out(db, access, doc)
