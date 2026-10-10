"""posting rules (versioned, approved by accountant) and link of entries to rule/source

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def _rls(table: str, *, update: bool, delete: bool) -> None:
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
    if delete:
        op.execute(
            f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING (organization_id = {CUR_ORG})"
        )


def upgrade() -> None:
    op.create_table(
        "posting_rule",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="DRAFT"),
        sa.Column("legal_basis", sa.Text()),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_posting_rule_id_org"),
        sa.UniqueConstraint(
            "organization_id", "rule_key", "version", name="uq_posting_rule_version"
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'RETIRED')", name="ck_posting_rule_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_posting_rule_version"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="ck_posting_rule_dates",
        ),
        # Утверждённое правило обязано иметь основание и утвердившего.
        sa.CheckConstraint(
            "status = 'DRAFT' OR (legal_basis IS NOT NULL AND length(btrim(legal_basis)) > 0 "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_posting_rule_approved",
        ),
    )
    op.create_table(
        "posting_rule_line",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(6), nullable=False),
        sa.Column("amount_source", sa.String(20), nullable=False),
        sa.Column("memo", sa.Text()),
        sa.ForeignKeyConstraint(
            ["rule_id", "organization_id"],
            ["posting_rule.id", "posting_rule.organization_id"],
            name="fk_posting_rule_line_rule",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["account.id", "account.organization_id"],
            name="fk_posting_rule_line_account",
        ),
        sa.UniqueConstraint("rule_id", "position", name="uq_posting_rule_line_position"),
        sa.CheckConstraint("side IN ('debit', 'credit')", name="ck_posting_rule_line_side"),
        sa.CheckConstraint(
            "amount_source IN ('total_amount', 'tax_total', 'total_minus_tax')",
            name="ck_posting_rule_line_source",
        ),
    )
    # Строки утверждённого правила менять нельзя (новая версия — новое правило).
    op.execute(
        """
        CREATE FUNCTION posting_rule_line_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE st text;
        BEGIN
            SELECT status INTO st FROM posting_rule
              WHERE id = COALESCE(NEW.rule_id, OLD.rule_id)
                AND organization_id = COALESCE(NEW.organization_id, OLD.organization_id);
            IF st IS DISTINCT FROM 'DRAFT' THEN
                RAISE EXCEPTION 'posting rule lines are frozen once the rule is approved';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER posting_rule_line_frozen BEFORE INSERT OR UPDATE OR DELETE ON posting_rule_line "
        "FOR EACH ROW EXECUTE FUNCTION posting_rule_line_frozen()"
    )

    op.add_column("journal_entry", sa.Column("rule_id", sa.Uuid()))
    op.add_column("journal_entry", sa.Column("rule_version", sa.Integer()))
    op.create_foreign_key(
        "fk_journal_entry_rule",
        "journal_entry",
        "posting_rule",
        ["rule_id", "organization_id"],
        ["id", "organization_id"],
    )
    op.create_check_constraint(
        "ck_journal_entry_rule_source",
        "journal_entry",
        "rule_id IS NULL OR (source_type IS NOT NULL AND source_id IS NOT NULL AND rule_version IS NOT NULL)",
    )
    op.create_index(
        "ix_journal_entry_source", "journal_entry", ["organization_id", "source_type", "source_id"]
    )

    _rls("posting_rule", update=True, delete=True)
    _rls("posting_rule_line", update=False, delete=True)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON posting_rule TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON posting_rule_line TO hisob_app")


def downgrade() -> None:
    op.drop_index("ix_journal_entry_source", table_name="journal_entry")
    op.drop_constraint("ck_journal_entry_rule_source", "journal_entry", type_="check")
    op.drop_constraint("fk_journal_entry_rule", "journal_entry", type_="foreignkey")
    op.drop_column("journal_entry", "rule_version")
    op.drop_column("journal_entry", "rule_id")
    op.execute("DROP TABLE IF EXISTS posting_rule_line CASCADE")
    op.execute("DROP TABLE IF EXISTS posting_rule CASCADE")
    op.execute("DROP FUNCTION IF EXISTS posting_rule_line_frozen()")
