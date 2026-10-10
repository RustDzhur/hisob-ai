"""products, product identifiers (barcodes) + RLS

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "product",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("sku", CITEXT(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("unit_of_measure.id"), nullable=False),
        sa.Column("price", sa.Numeric(20, 4)),
        sa.Column("price_currency", sa.String(3), sa.ForeignKey("currency.code")),
        sa.Column("ikpu", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_product_id_org"),
        sa.CheckConstraint("kind IN ('goods','service')", name="ck_product_kind"),
        sa.CheckConstraint(
            "(price IS NULL) = (price_currency IS NULL)", name="ck_product_price_with_currency"
        ),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_product_price_non_negative"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_product_name_not_blank"),
    )
    op.create_index("uq_product_org_sku", "product", ["organization_id", "sku"], unique=True)
    op.create_index("ix_product_org_name", "product", ["organization_id", "name"])

    op.create_table(
        "product_identifier",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("value", CITEXT(), nullable=False),
        # product и его идентификаторы всегда принадлежат одной организации
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_product_identifier_product",
        ),
        sa.UniqueConstraint("organization_id", "kind", "value", name="uq_product_identifier_value"),
        sa.CheckConstraint("kind IN ('barcode')", name="ck_product_identifier_kind"),
    )

    for table in ("product", "product_identifier"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_select ON {table} FOR SELECT USING (organization_id = {CUR_ORG})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
        )
    op.execute(
        f"CREATE POLICY product_update ON product FOR UPDATE "
        f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY product_identifier_delete ON product_identifier FOR DELETE USING (organization_id = {CUR_ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON product TO hisob_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON product_identifier TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS product_identifier CASCADE")
    op.execute("DROP TABLE IF EXISTS product CASCADE")
