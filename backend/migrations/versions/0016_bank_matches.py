"""bank reconciliation: matches between bank transactions and documents, rejected candidates

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def _rls(table: str, *, update: bool) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_select ON {table} FOR SELECT USING (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
    )
    if update:
        op.execute(
            f"CREATE POLICY {table}_update ON {table} FOR UPDATE "
            f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
        )


def upgrade() -> None:
    op.create_table(
        "bank_match",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),  # распределённая часть платежа, > 0
        sa.Column("status", sa.String(10), nullable=False, server_default="CONFIRMED"),
        sa.Column(
            "explanation", sa.dialects.postgresql.JSONB()
        ),  # причины, по которым предложение выглядело подходящим
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("cancelled_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["transaction_id", "organization_id"],
            ["bank_transaction.id", "bank_transaction.organization_id"],
            name="fk_bank_match_txn",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_bank_match_document",
        ),
        sa.CheckConstraint("amount > 0", name="ck_bank_match_positive"),
        sa.CheckConstraint("status IN ('CONFIRMED', 'CANCELLED')", name="ck_bank_match_status"),
        sa.CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_bank_match_cancelled",
        ),
    )
    op.create_index("ix_bank_match_txn", "bank_match", ["organization_id", "transaction_id"])
    op.create_index("ix_bank_match_document", "bank_match", ["organization_id", "document_id"])

    op.create_table(
        "bank_match_rejection",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("rejected_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "organization_id"],
            ["bank_transaction.id", "bank_transaction.organization_id"],
            name="fk_bank_rejection_txn",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_bank_rejection_document",
        ),
        sa.UniqueConstraint(
            "organization_id", "transaction_id", "document_id", name="uq_bank_rejection"
        ),
    )

    # Защита целостности в БД: сумма действующих сопоставлений не превышает ни платёж, ни документ,
    # а валюты совпадают (проверка в конце транзакции, поэтому набор строк вставляется целиком).
    op.execute(
        """
        CREATE FUNCTION bank_match_limits() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE t_amount numeric; t_cur text; d_total numeric; d_cur text; s_txn numeric; s_doc numeric;
        BEGIN
            IF NEW.status <> 'CONFIRMED' THEN RETURN NULL; END IF;
            SELECT abs(amount), currency INTO t_amount, t_cur FROM bank_transaction
              WHERE id = NEW.transaction_id AND organization_id = NEW.organization_id;
            SELECT total_amount, currency INTO d_total, d_cur FROM document
              WHERE id = NEW.document_id AND organization_id = NEW.organization_id;
            IF d_total IS NULL OR d_cur IS DISTINCT FROM t_cur THEN
                RAISE EXCEPTION 'bank match: document has no total or its currency differs from the payment';
            END IF;
            SELECT COALESCE(sum(amount), 0) INTO s_txn FROM bank_match
              WHERE transaction_id = NEW.transaction_id AND organization_id = NEW.organization_id AND status = 'CONFIRMED';
            SELECT COALESCE(sum(amount), 0) INTO s_doc FROM bank_match
              WHERE document_id = NEW.document_id AND organization_id = NEW.organization_id AND status = 'CONFIRMED';
            IF s_txn > t_amount THEN RAISE EXCEPTION 'bank match: allocated amount exceeds the payment'; END IF;
            IF s_doc > d_total THEN RAISE EXCEPTION 'bank match: allocated amount exceeds the document total'; END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER bank_match_limits AFTER INSERT ON bank_match "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bank_match_limits()"
    )
    # Сопоставление не меняется, кроме отмены.
    op.execute(
        """
        CREATE FUNCTION bank_match_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'bank_match is append-only'; END IF;
            IF OLD.status = 'CANCELLED' THEN RAISE EXCEPTION 'a cancelled bank match cannot be changed'; END IF;
            IF NEW.amount IS DISTINCT FROM OLD.amount OR NEW.transaction_id IS DISTINCT FROM OLD.transaction_id
               OR NEW.document_id IS DISTINCT FROM OLD.document_id THEN
                RAISE EXCEPTION 'bank match facts are immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER bank_match_guard BEFORE UPDATE OR DELETE ON bank_match "
        "FOR EACH ROW EXECUTE FUNCTION bank_match_guard()"
    )
    _rls("bank_match", update=True)
    _rls("bank_match_rejection", update=False)
    op.execute("GRANT SELECT, INSERT, UPDATE ON bank_match TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON bank_match_rejection TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bank_match_rejection CASCADE")
    op.execute("DROP TABLE IF EXISTS bank_match CASCADE")
    op.execute("DROP FUNCTION IF EXISTS bank_match_limits()")
    op.execute("DROP FUNCTION IF EXISTS bank_match_guard()")
