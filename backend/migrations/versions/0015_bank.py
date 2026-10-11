"""bank accounts, statement imports, bank transactions + RLS + permissions

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

NEW_PERMISSIONS = {
    "bank.read": "Просмотр банковских счетов, выписок и операций",
    "bank.manage": "Банковские счета и профили импорта",
    "bank.import": "Загрузка банковских выписок",
    "bank.reconcile": "Сверка банковских операций",
}
# Матрица — предложение (R13), переспросить после завершения проекта.
ROLE_PERMS = {
    "owner": list(NEW_PERMISSIONS),
    "org_admin": list(NEW_PERMISSIONS),
    "accountant": list(NEW_PERMISSIONS),
    "auditor": ["bank.read"],
}


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
        "bank_account",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("bank_name", sa.Text()),
        sa.Column("account_number", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currency.code"), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("import_profile", postgresql.JSONB()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_account_id_org"),
        sa.UniqueConstraint("organization_id", "account_number", name="uq_bank_account_number"),
        sa.CheckConstraint(
            "length(btrim(name)) > 0 AND length(btrim(account_number)) > 0",
            name="ck_bank_account_not_blank",
        ),
    )
    op.create_table(
        "bank_import",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("bank_account_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False),
        sa.Column("rows_duplicate", sa.Integer(), nullable=False),
        sa.Column("rows_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id", "organization_id"],
            ["bank_account.id", "bank_account.organization_id"],
            name="fk_bank_import_account",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_import_id_org"),
        sa.UniqueConstraint(
            "organization_id", "bank_account_id", "sha256", name="uq_bank_import_file"
        ),
    )
    op.create_table(
        "bank_transaction",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("bank_account_id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column(
            "amount", sa.Numeric(20, 4), nullable=False
        ),  # со знаком: плюс — поступление, минус — списание
        sa.Column("currency", sa.String(3), sa.ForeignKey("currency.code"), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("reference", sa.Text()),
        sa.Column("counterparty_name", sa.Text()),
        sa.Column("counterparty_tin", sa.Text()),
        sa.Column("counterparty_id", sa.Uuid()),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="UNMATCHED"),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id", "organization_id"],
            ["bank_account.id", "bank_account.organization_id"],
            name="fk_bank_txn_account",
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "organization_id"],
            ["bank_import.id", "bank_import.organization_id"],
            name="fk_bank_txn_import",
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id", "organization_id"],
            ["counterparty.id", "counterparty.organization_id"],
            name="fk_bank_txn_counterparty",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_txn_id_org"),
        sa.UniqueConstraint(
            "organization_id", "bank_account_id", "row_hash", name="uq_bank_txn_row_hash"
        ),
        sa.CheckConstraint("amount <> 0", name="ck_bank_txn_nonzero"),
        sa.CheckConstraint(
            "status IN ('UNMATCHED', 'MATCHED', 'IGNORED')", name="ck_bank_txn_status"
        ),
    )
    op.create_index(
        "ix_bank_txn_account_date",
        "bank_transaction",
        ["organization_id", "bank_account_id", "txn_date"],
    )
    op.create_index("ix_bank_txn_status", "bank_transaction", ["organization_id", "status"])

    # Операции выписки неизменяемы (кроме статуса сверки): суммы, даты и назначение менять нельзя.
    op.execute(
        """
        CREATE FUNCTION bank_txn_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'bank_transaction is append-only';
            END IF;
            IF NEW.amount IS DISTINCT FROM OLD.amount OR NEW.txn_date IS DISTINCT FROM OLD.txn_date
               OR NEW.currency IS DISTINCT FROM OLD.currency OR NEW.row_hash IS DISTINCT FROM OLD.row_hash
               OR NEW.description IS DISTINCT FROM OLD.description OR NEW.reference IS DISTINCT FROM OLD.reference
               OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id OR NEW.import_id IS DISTINCT FROM OLD.import_id THEN
                RAISE EXCEPTION 'bank_transaction facts are immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER bank_txn_guard BEFORE UPDATE OR DELETE ON bank_transaction "
        "FOR EACH ROW EXECUTE FUNCTION bank_txn_guard()"
    )

    # Запись об импорте: после вставки операций обновляются только счётчики загрузки.
    op.execute(
        """
        CREATE FUNCTION bank_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'bank_import is append-only';
            END IF;
            IF NEW.sha256 IS DISTINCT FROM OLD.sha256 OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id
               OR NEW.filename IS DISTINCT FROM OLD.filename OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
               OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.rows_total IS DISTINCT FROM OLD.rows_total THEN
                RAISE EXCEPTION 'bank_import facts are immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER bank_import_guard BEFORE UPDATE OR DELETE ON bank_import "
        "FOR EACH ROW EXECUTE FUNCTION bank_import_guard()"
    )

    _rls("bank_account", update=True)
    _rls("bank_import", update=True)
    _rls("bank_transaction", update=True)
    op.execute("GRANT SELECT, INSERT, UPDATE ON bank_account TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON bank_import TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON bank_transaction TO hisob_app")

    perm = sa.table("permission", sa.column("code", sa.Text), sa.column("description", sa.Text))
    op.bulk_insert(perm, [dict(code=c, description=d) for c, d in NEW_PERMISSIONS.items()])
    for role_code, perms in ROLE_PERMS.items():
        for p in perms:
            op.execute(
                sa.text(
                    "INSERT INTO role_permission (role_id, permission_code) SELECT id, :p FROM role WHERE code = :r"
                ).bindparams(p=p, r=role_code)
            )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'bank.%'")
    op.execute("DELETE FROM permission WHERE code LIKE 'bank.%'")
    for t in ("bank_transaction", "bank_import", "bank_account"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS bank_txn_guard()")
    op.execute("DROP FUNCTION IF EXISTS bank_import_guard()")
