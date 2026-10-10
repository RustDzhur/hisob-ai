"""accounting engine: accounts, periods, immutable journal, balance control + RLS + permissions

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

NEW_PERMISSIONS = {
    "accounting.read": "Просмотр плана счетов, проводок и отчётов",
    "accounting.post": "Ввод проводок и сторно",
    "accounting.manage": "План счетов, закрытие и открытие периодов",
}
# Матрица — предложение (R3 в docs/09-open-questions-recheck.md), переспросить после завершения проекта.
ROLE_PERMS = {
    "owner": list(NEW_PERMISSIONS),
    "org_admin": list(NEW_PERMISSIONS),
    "accountant": list(NEW_PERMISSIONS),
    "auditor": ["accounting.read"],
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
        "account",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("allow_posting", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_account_id_org"),
        sa.UniqueConstraint("organization_id", "code", name="uq_account_org_code"),
        sa.ForeignKeyConstraint(
            ["parent_id", "organization_id"],
            ["account.id", "account.organization_id"],
            name="fk_account_parent",
        ),
        sa.CheckConstraint(
            "length(btrim(code)) > 0 AND length(btrim(name)) > 0", name="ck_account_not_blank"
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_account_not_own_parent"
        ),
    )

    op.create_table(
        "accounting_period",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="OPEN"),
        sa.Column("closed_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_period_id_org"),
        sa.UniqueConstraint("organization_id", "start_date", name="uq_period_org_start"),
        sa.CheckConstraint("end_date >= start_date", name="ck_period_dates"),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_period_status"),
        sa.CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL AND closed_by IS NOT NULL)",
            name="ck_period_closed",
        ),
    )

    op.create_table(
        "accounting_counter",
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), primary_key=True),
        sa.Column("last_number", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.create_table(
        "journal_entry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currency.code"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reverses_id", sa.Uuid()),
        sa.Column("source_type", sa.Text()),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_journal_entry_id_org"),
        sa.UniqueConstraint("organization_id", "number", name="uq_journal_entry_number"),
        sa.UniqueConstraint("reverses_id", name="uq_journal_entry_reverses"),
        sa.ForeignKeyConstraint(
            ["period_id", "organization_id"],
            ["accounting_period.id", "accounting_period.organization_id"],
            name="fk_journal_entry_period",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_id", "organization_id"],
            ["journal_entry.id", "journal_entry.organization_id"],
            name="fk_journal_entry_reverses",
        ),
        sa.CheckConstraint("length(btrim(description)) > 0", name="ck_journal_entry_description"),
    )
    op.create_index(
        "ix_journal_entry_org_date", "journal_entry", ["organization_id", "entry_date", "number"]
    )

    op.create_table(
        "journal_line",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("debit", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("credit", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("memo", sa.Text()),
        sa.ForeignKeyConstraint(
            ["entry_id", "organization_id"],
            ["journal_entry.id", "journal_entry.organization_id"],
            name="fk_journal_line_entry",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["account.id", "account.organization_id"],
            name="fk_journal_line_account",
        ),
        sa.UniqueConstraint("entry_id", "position", name="uq_journal_line_position"),
        sa.CheckConstraint(
            "debit >= 0 AND credit >= 0 AND ((debit > 0) <> (credit > 0))",
            name="ck_journal_line_one_side",
        ),
    )
    op.create_index("ix_journal_line_account", "journal_line", ["organization_id", "account_id"])

    # --- инварианты на уровне БД ---
    # 1. Проведённое неизменяемо.
    op.execute(
        """
        CREATE FUNCTION journal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END $$
        """
    )
    for t in ("journal_entry", "journal_line"):
        op.execute(
            f"CREATE TRIGGER {t}_no_update_delete BEFORE UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION journal_immutable()"
        )
    # 2. Дебет = кредит: проверка в конце транзакции (после вставки всех строк записи).
    op.execute(
        """
        CREATE FUNCTION journal_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE d numeric; c numeric;
        BEGIN
            SELECT COALESCE(sum(debit), 0), COALESCE(sum(credit), 0) INTO d, c
            FROM journal_line WHERE entry_id = NEW.entry_id;
            IF d <> c THEN
                RAISE EXCEPTION 'journal entry is not balanced: debit % <> credit %', d, c;
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER journal_line_balanced AFTER INSERT ON journal_line "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_balanced()"
    )
    # 2б. Проводка без строк недопустима (проверка в конце транзакции).
    op.execute(
        """
        CREATE FUNCTION journal_entry_has_lines() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM journal_line WHERE entry_id = NEW.id) THEN
                RAISE EXCEPTION 'journal entry has no lines';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER journal_entry_lines AFTER INSERT ON journal_entry "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_has_lines()"
    )
    # 3. Закрытый период и дата вне периода: запись невозможна даже в обход приложения.
    op.execute(
        """
        CREATE FUNCTION journal_entry_period_check() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE p accounting_period%ROWTYPE;
        BEGIN
            SELECT * INTO p FROM accounting_period WHERE id = NEW.period_id AND organization_id = NEW.organization_id;
            IF p.id IS NULL THEN RAISE EXCEPTION 'accounting period not found'; END IF;
            IF p.status <> 'OPEN' THEN RAISE EXCEPTION 'accounting period is closed'; END IF;
            IF NEW.entry_date < p.start_date OR NEW.entry_date > p.end_date THEN
                RAISE EXCEPTION 'entry date is outside the accounting period';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER journal_entry_period BEFORE INSERT ON journal_entry "
        "FOR EACH ROW EXECUTE FUNCTION journal_entry_period_check()"
    )

    _rls("account", update=True)
    _rls("accounting_period", update=True)
    _rls("accounting_counter", update=True)
    _rls("journal_entry", update=False)
    _rls("journal_line", update=False)
    op.execute("GRANT SELECT, INSERT, UPDATE ON account TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON accounting_period TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON accounting_counter TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON journal_entry TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON journal_line TO hisob_app")

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
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'accounting.%'")
    op.execute("DELETE FROM permission WHERE code LIKE 'accounting.%'")
    for t in (
        "journal_line",
        "journal_entry",
        "accounting_counter",
        "accounting_period",
        "account",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS journal_entry_has_lines()")
    op.execute("DROP FUNCTION IF EXISTS journal_entry_period_check()")
    op.execute("DROP FUNCTION IF EXISTS journal_entry_balanced()")
    op.execute("DROP FUNCTION IF EXISTS journal_immutable()")
