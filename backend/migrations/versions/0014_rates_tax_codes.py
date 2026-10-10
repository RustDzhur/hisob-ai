"""exchange rates and tax codes (reference data entered/confirmed by the accountant)

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for verb, clause in (
        ("select", f"FOR SELECT USING (organization_id = {CUR_ORG})"),
        ("insert", f"FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"),
        (
            "update",
            f"FOR UPDATE USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})",
        ),
        ("delete", f"FOR DELETE USING (organization_id = {CUR_ORG})"),
    ):
        op.execute(f"CREATE POLICY {table}_{verb} ON {table} {clause}")


def upgrade() -> None:
    op.create_table(
        "exchange_rate",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("currency_code", sa.String(3), sa.ForeignKey("currency.code"), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("nominal", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rate", sa.Numeric(20, 6), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "organization_id", "currency_code", "rate_date", name="uq_exchange_rate_day"
        ),
        sa.CheckConstraint("nominal > 0 AND rate > 0", name="ck_exchange_rate_positive"),
        sa.CheckConstraint("source IN ('manual', 'cbu')", name="ck_exchange_rate_source"),
    )
    op.create_index(
        "ix_exchange_rate_lookup",
        "exchange_rate",
        ["organization_id", "currency_code", "rate_date"],
    )

    op.create_table(
        "tax_code",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="vat"),
        sa.Column("rate", sa.Numeric(7, 4), nullable=False),  # проценты: 12 = 12 %
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_note", sa.Text()),
        sa.Column("status", sa.String(10), nullable=False, server_default="DRAFT"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "code", name="uq_tax_code_org_code"),
        sa.CheckConstraint("rate >= 0 AND rate <= 100", name="ck_tax_code_rate"),
        sa.CheckConstraint("kind IN ('vat')", name="ck_tax_code_kind"),
        sa.CheckConstraint("status IN ('DRAFT', 'CONFIRMED')", name="ck_tax_code_status"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="ck_tax_code_dates",
        ),
        sa.CheckConstraint(
            "(status = 'CONFIRMED') = (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)",
            name="ck_tax_code_confirmed",
        ),
        sa.CheckConstraint(
            "NOT is_default OR status = 'CONFIRMED'", name="ck_tax_code_default_confirmed"
        ),
    )
    op.create_index(
        "uq_tax_code_default",
        "tax_code",
        ["organization_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    for t in ("exchange_rate", "tax_code"):
        _rls(t)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tax_code CASCADE")
    op.execute("DROP TABLE IF EXISTS exchange_rate CASCADE")
