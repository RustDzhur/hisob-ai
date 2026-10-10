"""Чтение результата распознавания для карточки документа."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.documents.models import AIJob, AIResult, ExtractedField
from app.modules.documents.schemas import ExtractedFieldOut, ExtractionJobOut, ExtractionView


def build_view(db: Session, doc_id: uuid.UUID) -> ExtractionView:
    job = db.scalar(
        select(AIJob).where(AIJob.document_id == doc_id).order_by(AIJob.created_at.desc()).limit(1)
    )
    if job is None:
        return ExtractionView()
    out = ExtractionView(
        job=ExtractionJobOut(
            id=job.id,
            status=job.status,
            provider=job.provider,
            model=job.model,
            mock=job.provider == "mock",
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )
    )
    if job.status != "succeeded":
        return out
    fields = db.scalars(
        select(ExtractedField).where(ExtractedField.job_id == job.id).order_by(ExtractedField.path)
    )
    out.fields = [
        ExtractedFieldOut(
            path=f.path,
            value=f.value,
            confidence=float(f.confidence) if f.confidence is not None else None,
            evidence=f.evidence,
            validation=f.validation,
            applied=f.applied,
        )
        for f in fields
    ]
    result = db.get(AIResult, job.id)
    if result is not None:
        out.counterparty_candidates = list(result.matches.get("counterparty", []))
        out.line_matches = dict(result.matches.get("lines", {}))
    return out
