import uuid
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.modules.documents import file_checks
from app.modules.documents import service as svc
from app.modules.documents.models import DOC_TYPES
from app.modules.documents.schemas import (
    ActionIn,
    ApproveIn,
    DocumentListItem,
    DocumentOut,
    DocumentUpdate,
    DuplicateIn,
    RejectIn,
)
from app.modules.identity.deps import CurrentPrincipal
from app.platform.config import get_settings
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.errors import AppError
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/documents", tags=["documents"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


@router.get("", response_model=Page[DocumentListItem])
def list_documents(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[
        Literal["active", "needs_review", "approved", "rejected", "duplicate", "archived", "all"],
        Query(),
    ] = "active",
    doc_type: Annotated[str | None, Query(max_length=20)] = None,
    counterparty_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[DocumentListItem]:
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise AppError(
            422,
            "validation_error",
            "Неизвестный тип документа.",
            [{"field": "doc_type", "code": "invalid", "message": "Неизвестный тип документа."}],
        )
    return svc.list_documents(
        db,
        principal,
        org_id,
        lq,
        status=status,
        doc_type=doc_type,
        counterparty_id=counterparty_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[str, Form()] = "other",
) -> DocumentOut:
    if doc_type not in DOC_TYPES:
        raise AppError(
            422,
            "validation_error",
            "Неизвестный тип документа.",
            [{"field": "doc_type", "code": "invalid", "message": "Неизвестный тип документа."}],
        )
    limit = get_settings().max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:  # читаем с запасом в 1 байт, чтобы не держать в памяти огромный файл
            raise AppError(
                413,
                "file_too_large",
                "Файл слишком большой.",
                [
                    {
                        "field": "file",
                        "code": "file_too_large",
                        "message": "Файл слишком большой.",
                        "params": {"max_mb": limit // (1024 * 1024)},
                    }
                ],
            )
        chunks.append(chunk)
    return svc.upload_document(
        db,
        principal,
        org_id,
        filename=file.filename or "file",
        data=b"".join(chunks),
        doc_type=doc_type,
    )


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(
    org_id: uuid.UUID, doc_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> DocumentOut:
    return svc.get_document(db, principal, org_id, doc_id)


@router.get("/{doc_id}/file")
def download_file(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    download: bool = False,
) -> StreamingResponse:
    f, stream = svc.open_file(db, principal, org_id, doc_id)
    inline = file_checks.KINDS[svc.kind_of(f.content_type)][2] and not download
    # filename* — RFC 5987: безопасно для кириллицы и спецсимволов
    from urllib.parse import quote

    disposition = (
        f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(f.original_name)}"
    )

    def body() -> Iterator[bytes]:
        yield from stream

    return StreamingResponse(
        body(),
        media_type=f.content_type,
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(f.size_bytes),
            "X-Content-Type-Options": "nosniff",
            # sandbox ломает встроенный просмотр PDF в браузере; для PDF активное содержимое
            # отсекается при загрузке, для остальных типов оставляем полную изоляцию
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'"
                if f.content_type == "application/pdf"
                else "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.patch("/{doc_id}", response_model=DocumentOut)
def update_document(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: DocumentUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.update_document(db, principal, org_id, doc_id, body)


@router.post("/{doc_id}/approve", response_model=DocumentOut)
def approve(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: ApproveIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.approve(db, principal, org_id, doc_id, body.expected_version, body.acknowledged)


@router.post("/{doc_id}/reject", response_model=DocumentOut)
def reject(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: RejectIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.reject(db, principal, org_id, doc_id, body.expected_version, body.reason)


@router.post("/{doc_id}/mark-duplicate", response_model=DocumentOut)
def mark_duplicate(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: DuplicateIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.mark_duplicate(
        db, principal, org_id, doc_id, body.expected_version, body.original_id
    )


@router.post("/{doc_id}/reopen", response_model=DocumentOut)
def reopen(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: ActionIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.reopen(db, principal, org_id, doc_id, body.expected_version)


@router.post("/{doc_id}/archive", response_model=DocumentOut)
def archive(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: ActionIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.archive(db, principal, org_id, doc_id, body.expected_version)


@router.post("/{doc_id}/restore", response_model=DocumentOut)
def restore(
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: ActionIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> DocumentOut:
    return svc.restore(db, principal, org_id, doc_id, body.expected_version)
