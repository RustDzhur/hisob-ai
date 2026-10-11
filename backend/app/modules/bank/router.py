import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.modules.bank import reconcile
from app.modules.bank import service as svc
from app.modules.bank.parsing import ImportProfile
from app.modules.bank.schemas import (
    BankAccountCreate,
    BankAccountOut,
    BankAccountUpdate,
    DocumentBankOut,
    HeadersOut,
    IgnoreIn,
    ImportOut,
    MatchCreate,
    MergeCreate,
    PreviewOut,
    RejectCandidateIn,
    TransactionDetailOut,
    TransactionOut,
)
from app.modules.identity.deps import CurrentPrincipal
from app.platform.csrf import require_csrf
from app.platform.db import get_db
from app.platform.errors import AppError
from app.platform.listing import ListParams, Page

router = APIRouter(prefix="/organizations/{org_id}/bank", tags=["bank"])
DbSession = Annotated[Session, Depends(get_db)]
Csrf = Annotated[None, Depends(require_csrf)]


async def _read(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > svc.MAX_FILE:  # не держим в памяти огромный файл
            raise AppError(413, "file_too_large", "Файл выписки слишком большой.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/accounts", response_model=Page[BankAccountOut])
def list_accounts(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    status: Annotated[Literal["active", "archived", "all"], Query()] = "active",
) -> Page[BankAccountOut]:
    return svc.list_accounts(db, principal, org_id, lq, status=status)


@router.post("/accounts", response_model=BankAccountOut, status_code=201)
def create_account(
    org_id: uuid.UUID,
    body: BankAccountCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> BankAccountOut:
    return svc.create_account(db, principal, org_id, body)


@router.get("/accounts/{account_id}", response_model=BankAccountOut)
def get_account(
    org_id: uuid.UUID, account_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> BankAccountOut:
    return svc.get_account(db, principal, org_id, account_id)


@router.patch("/accounts/{account_id}", response_model=BankAccountOut)
def update_account(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    body: BankAccountUpdate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> BankAccountOut:
    return svc.update_account(db, principal, org_id, account_id, body)


@router.post("/accounts/{account_id}/archive", response_model=BankAccountOut)
def archive_account(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> BankAccountOut:
    return svc.set_archived(db, principal, org_id, account_id, True)


@router.post("/accounts/{account_id}/restore", response_model=BankAccountOut)
def restore_account(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> BankAccountOut:
    return svc.set_archived(db, principal, org_id, account_id, False)


@router.get("/accounts/{account_id}/profile", response_model=ImportProfile | None)
def get_profile(
    org_id: uuid.UUID, account_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> ImportProfile | None:
    return svc.get_profile(db, principal, org_id, account_id)


@router.put("/accounts/{account_id}/profile", response_model=ImportProfile)
def put_profile(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    body: ImportProfile,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> ImportProfile:
    return svc.set_profile(db, principal, org_id, account_id, body)


@router.post("/accounts/{account_id}/headers", response_model=HeadersOut)
async def file_headers(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    profile: Annotated[str, Form()] = "{}",
) -> HeadersOut:
    data = await _read(file)
    return svc.headers(db, principal, org_id, account_id, data, file.filename or "file", profile)


@router.post("/accounts/{account_id}/preview", response_model=PreviewOut)
async def preview(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    profile: Annotated[str | None, Form()] = None,
) -> PreviewOut:
    data = await _read(file)
    return svc.preview(db, principal, org_id, account_id, data, file.filename or "file", profile)


@router.post("/accounts/{account_id}/imports", response_model=ImportOut, status_code=201)
async def import_statement(
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    profile: Annotated[str | None, Form()] = None,
    skip_errors: Annotated[bool, Form()] = False,
) -> ImportOut:
    data = await _read(file)
    return svc.import_statement(
        db, principal, org_id, account_id, data, file.filename or "file", profile, skip_errors
    )


@router.get("/accounts/{account_id}/imports", response_model=list[ImportOut])
def list_imports(
    org_id: uuid.UUID, account_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> list[ImportOut]:
    return svc.list_imports(db, principal, org_id, account_id)


@router.get("/transactions", response_model=Page[TransactionOut])
def list_transactions(
    org_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    lq: ListParams,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    import_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[Literal["all", "UNMATCHED", "MATCHED", "IGNORED"], Query()] = "all",
    direction: Annotated[Literal["all", "in", "out"], Query()] = "all",
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Page[TransactionOut]:
    return svc.list_transactions(
        db,
        principal,
        org_id,
        lq,
        account_id=account_id,
        status=status,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
        import_id=import_id,
    )


# ---------------- сверка (срез 6.2) ----------------


@router.get("/transactions/{transaction_id}", response_model=TransactionDetailOut)
def transaction_detail(
    org_id: uuid.UUID, transaction_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> TransactionDetailOut:
    return reconcile.transaction_detail(db, principal, org_id, transaction_id)


@router.post(
    "/transactions/{transaction_id}/matches", response_model=TransactionDetailOut, status_code=201
)
def confirm_matches(
    org_id: uuid.UUID,
    transaction_id: uuid.UUID,
    body: MatchCreate,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> TransactionDetailOut:
    return reconcile.confirm(db, principal, org_id, transaction_id, body)


@router.post("/matches/merge", response_model=DocumentBankOut, status_code=201)
def merge_matches(
    org_id: uuid.UUID, body: MergeCreate, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> DocumentBankOut:
    return reconcile.merge(db, principal, org_id, body)


@router.post("/matches/{match_id}/cancel", response_model=TransactionDetailOut)
def cancel_match(
    org_id: uuid.UUID, match_id: uuid.UUID, principal: CurrentPrincipal, _csrf: Csrf, db: DbSession
) -> TransactionDetailOut:
    return reconcile.cancel(db, principal, org_id, match_id)


@router.post("/transactions/{transaction_id}/reject-candidate", response_model=TransactionDetailOut)
def reject_candidate(
    org_id: uuid.UUID,
    transaction_id: uuid.UUID,
    body: RejectCandidateIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> TransactionDetailOut:
    return reconcile.reject_candidate(db, principal, org_id, transaction_id, body)


@router.post("/transactions/{transaction_id}/ignore", response_model=TransactionDetailOut)
def ignore_transaction(
    org_id: uuid.UUID,
    transaction_id: uuid.UUID,
    body: IgnoreIn,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> TransactionDetailOut:
    return reconcile.set_ignored(db, principal, org_id, transaction_id, body, True)


@router.post("/transactions/{transaction_id}/unignore", response_model=TransactionDetailOut)
def unignore_transaction(
    org_id: uuid.UUID,
    transaction_id: uuid.UUID,
    principal: CurrentPrincipal,
    _csrf: Csrf,
    db: DbSession,
) -> TransactionDetailOut:
    return reconcile.set_ignored(db, principal, org_id, transaction_id, IgnoreIn(), False)


@router.get("/documents/{document_id}", response_model=DocumentBankOut)
def document_bank(
    org_id: uuid.UUID, document_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> DocumentBankOut:
    return reconcile.document_bank(db, principal, org_id, document_id)
