"""stock counts (inventory), min levels, reservations

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
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
    # Строки корректирующей операции могут быть отрицательными (расхождение инвентаризации).
    op.add_column(
        "stock_operation_line",
        sa.Column("is_adjustment", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("ck_stock_line_positive", "stock_operation_line", type_="check")
    op.create_check_constraint(
        "ck_stock_line_quantity",
        "stock_operation_line",
        "quantity <> 0 AND (quantity > 0 OR is_adjustment)",
    )

    op.create_table(
        "stock_count",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("notes", sa.Text()),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("operation_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("posted_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_stock_count_id_org"),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_count_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_count_operation",
        ),
        sa.CheckConstraint("status IN ('IN_PROGRESS', 'POSTED')", name="ck_stock_count_status"),
        sa.CheckConstraint(
            "(status = 'POSTED') = (posted_at IS NOT NULL AND posted_by IS NOT NULL)",
            name="ck_stock_count_posted",
        ),
    )
    # По складу одновременно ведётся одна инвентаризация: иначе корректировки конфликтовали бы.
    op.create_index(
        "uq_stock_count_active_warehouse",
        "stock_count",
        ["organization_id", "warehouse_id"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )

    op.create_table(
        "stock_count_line",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("count_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("expected_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(20, 6)),
        sa.Column("applied_delta", sa.Numeric(20, 6)),
        sa.ForeignKeyConstraint(
            ["count_id", "organization_id"],
            ["stock_count.id", "stock_count.organization_id"],
            name="fk_stock_count_line_count",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_count_line_product",
        ),
        sa.UniqueConstraint("count_id", "product_id", name="uq_stock_count_line_product"),
        sa.CheckConstraint(
            "counted_quantity IS NULL OR counted_quantity >= 0", name="ck_stock_count_line_counted"
        ),
    )

    op.create_table(
        "stock_min_level",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("min_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("organization_id", "warehouse_id", "product_id"),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_min_level_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_min_level_product",
        ),
        sa.CheckConstraint("min_quantity > 0", name="ck_stock_min_level_positive"),
    )

    op.create_table(
        "stock_reservation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("released_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_reservation_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_reservation_product",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_stock_reservation_positive"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RELEASED')", name="ck_stock_reservation_status"),
        sa.CheckConstraint(
            "(status = 'RELEASED') = (released_at IS NOT NULL AND released_by IS NOT NULL)",
            name="ck_stock_reservation_released",
        ),
    )
    op.create_index(
        "ix_stock_reservation_active",
        "stock_reservation",
        ["organization_id", "warehouse_id", "product_id"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    _rls("stock_count", update=True, delete=True)
    _rls("stock_count_line", update=True, delete=True)
    _rls("stock_min_level", update=True, delete=True)
    _rls("stock_reservation", update=True, delete=False)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_count TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_count_line TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_min_level TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON stock_reservation TO hisob_app")


def downgrade() -> None:
    for t in ("stock_reservation", "stock_min_level", "stock_count_line", "stock_count"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.drop_constraint("ck_stock_line_quantity", "stock_operation_line", type_="check")
    op.create_check_constraint("ck_stock_line_positive", "stock_operation_line", "quantity > 0")
    op.drop_column("stock_operation_line", "is_adjustment")
