"""warehouses + RLS

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "warehouse",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("name", CITEXT(), nullable=False),
        sa.Column("address", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_warehouse_name_not_blank"),
    )
    op.create_index("uq_warehouse_org_name", "warehouse", ["organization_id", "name"], unique=True)
    op.execute("ALTER TABLE warehouse ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE warehouse FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY warehouse_select ON warehouse FOR SELECT USING (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY warehouse_insert ON warehouse FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY warehouse_update ON warehouse FOR UPDATE "
        f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON warehouse TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS warehouse CASCADE")
