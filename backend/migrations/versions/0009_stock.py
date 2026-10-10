"""stock: operations, lines, append-only movements, balances + RLS + permissions

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

KINDS = (
    "receipt",
    "issue",
    "transfer",
    "supplier_return",
    "customer_return",
    "count_adjustment",
)
NEW_PERMISSIONS = {
    "stock.read": "Просмотр остатков и движений",
    "stock.manage": "Создание и правка складских операций (черновики)",
    "stock.post": "Проведение складских операций",
}
# Матрица предложена в docs/08-stage4-plan.md (Q24 ждёт подтверждения).
ROLE_PERMS = {
    "owner": list(NEW_PERMISSIONS),
    "org_admin": list(NEW_PERMISSIONS),
    "accountant": list(NEW_PERMISSIONS),
    "storekeeper": list(NEW_PERMISSIONS),
    "sales_purchase_manager": ["stock.read", "stock.manage"],
    "document_operator": ["stock.read"],
    "auditor": ["stock.read"],
}


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_unique_constraint("uq_warehouse_id_org", "warehouse", ["id", "organization_id"])

    op.create_table(
        "stock_operation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="DRAFT"),
        sa.Column("op_date", sa.Date(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("to_warehouse_id", sa.Uuid()),
        sa.Column("reference", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("posted_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_stock_operation_id_org"),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_operation_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["to_warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_operation_to_warehouse",
        ),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_stock_operation_kind"),
        sa.CheckConstraint("status IN ('DRAFT', 'POSTED')", name="ck_stock_operation_status"),
        sa.CheckConstraint(
            "(kind = 'transfer') = (to_warehouse_id IS NOT NULL)", name="ck_stock_operation_to_wh"
        ),
        sa.CheckConstraint(
            "to_warehouse_id IS NULL OR to_warehouse_id <> warehouse_id",
            name="ck_stock_operation_distinct_wh",
        ),
        sa.CheckConstraint(
            "(status = 'POSTED') = (posted_at IS NOT NULL AND posted_by IS NOT NULL)",
            name="ck_stock_operation_posted",
        ),
    )
    op.create_index(
        "ix_stock_operation_org_date", "stock_operation", ["organization_id", "op_date"]
    )

    op.create_table(
        "stock_operation_line",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_line_operation",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_line_product",
        ),
        sa.UniqueConstraint("operation_id", "position", name="uq_stock_line_position"),
        sa.CheckConstraint("quantity > 0", name="ck_stock_line_positive"),
    )

    op.create_table(
        "stock_movement",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(20, 6), nullable=False),
        sa.Column("op_date", sa.Date(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "organization_id"],
            ["stock_operation.id", "stock_operation.organization_id"],
            name="fk_stock_movement_operation",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_movement_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_movement_product",
        ),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_stock_movement_nonzero"),
    )
    op.create_index(
        "ix_stock_movement_product",
        "stock_movement",
        ["organization_id", "product_id", "occurred_at"],
    )
    op.create_index(
        "ix_stock_movement_warehouse", "stock_movement", ["organization_id", "warehouse_id"]
    )
    op.create_index("ix_stock_movement_operation", "stock_movement", ["operation_id"])
    op.execute(
        """
        CREATE FUNCTION stock_movement_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stock_movement is append-only';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER stock_movement_no_update_delete
        BEFORE UPDATE OR DELETE ON stock_movement
        FOR EACH ROW EXECUTE FUNCTION stock_movement_immutable()
        """
    )

    # Производная таблица: меняется только вместе с вставкой движения, под блокировкой строки.
    op.create_table(
        "stock_balance",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("organization_id", "warehouse_id", "product_id"),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "organization_id"],
            ["warehouse.id", "warehouse.organization_id"],
            name="fk_stock_balance_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_stock_balance_product",
        ),
    )

    for table in ("stock_operation", "stock_operation_line", "stock_movement", "stock_balance"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_select ON {table} FOR SELECT USING (organization_id = {CUR_ORG})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
        )
    for table in ("stock_operation", "stock_balance"):
        op.execute(
            f"CREATE POLICY {table}_update ON {table} FOR UPDATE "
            f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
        )
    for table in ("stock_operation", "stock_operation_line"):
        op.execute(
            f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING (organization_id = {CUR_ORG})"
        )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_operation TO hisob_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON stock_operation_line TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON stock_movement TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON stock_balance TO hisob_app")

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
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'stock.%'")
    op.execute("DELETE FROM permission WHERE code LIKE 'stock.%'")
    for t in ("stock_balance", "stock_movement", "stock_operation_line", "stock_operation"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS stock_movement_immutable()")
    op.drop_constraint("uq_warehouse_id_org", "warehouse", type_="unique")
