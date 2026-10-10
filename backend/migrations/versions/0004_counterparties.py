"""counterparties + RLS

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "counterparty",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tin", sa.Text()),
        sa.Column("is_customer", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_supplier", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("address", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_counterparty_name_not_blank"),
    )
    op.create_index(
        "uq_counterparty_org_tin",
        "counterparty",
        ["organization_id", "tin"],
        unique=True,
        postgresql_where=sa.text("tin IS NOT NULL"),
    )
    op.create_index("ix_counterparty_org_name", "counterparty", ["organization_id", "name"])

    op.execute("ALTER TABLE counterparty ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE counterparty FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY counterparty_select ON counterparty FOR SELECT USING (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY counterparty_insert ON counterparty FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY counterparty_update ON counterparty FOR UPDATE "
        f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON counterparty TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS counterparty CASCADE")
