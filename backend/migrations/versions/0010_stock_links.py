"""stock operations: link to source document, reversal (storno)

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_operation", sa.Column("source_document_id", sa.Uuid()))
    op.add_column("stock_operation", sa.Column("reverses_id", sa.Uuid()))
    op.add_column("stock_operation", sa.Column("reversed_by_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_stock_operation_document",
        "stock_operation",
        "document",
        ["source_document_id", "organization_id"],
        ["id", "organization_id"],
    )
    op.create_foreign_key(
        "fk_stock_operation_reverses",
        "stock_operation",
        "stock_operation",
        ["reverses_id", "organization_id"],
        ["id", "organization_id"],
    )
    op.create_foreign_key(
        "fk_stock_operation_reversed_by",
        "stock_operation",
        "stock_operation",
        ["reversed_by_id", "organization_id"],
        ["id", "organization_id"],
    )
    # Документ нельзя оприходовать/списать дважды одним видом, пока действует прежняя операция;
    # после сторно (reversed_by_id задан) можно оформить заново.
    op.create_index(
        "uq_stock_operation_document_kind",
        "stock_operation",
        ["organization_id", "source_document_id", "kind"],
        unique=True,
        postgresql_where=sa.text(
            "source_document_id IS NOT NULL AND reversed_by_id IS NULL AND reverses_id IS NULL"
        ),
    )
    # Операцию можно сторнировать один раз.
    op.create_index(
        "uq_stock_operation_reverses",
        "stock_operation",
        ["reverses_id"],
        unique=True,
        postgresql_where=sa.text("reverses_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_stock_operation_reversal_posted",
        "stock_operation",
        "(reverses_id IS NULL AND reversed_by_id IS NULL) OR status = 'POSTED'",
    )
    op.create_check_constraint(
        "ck_stock_operation_reversal_exclusive",
        "stock_operation",
        "reverses_id IS NULL OR (reversed_by_id IS NULL AND source_document_id IS NULL)",
    )
    op.create_index("ix_stock_operation_document", "stock_operation", ["source_document_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_operation_document", table_name="stock_operation")
    op.drop_constraint("ck_stock_operation_reversal_exclusive", "stock_operation", type_="check")
    op.drop_constraint("ck_stock_operation_reversal_posted", "stock_operation", type_="check")
    op.drop_index("uq_stock_operation_reverses", table_name="stock_operation")
    op.drop_index("uq_stock_operation_document_kind", table_name="stock_operation")
    op.drop_constraint("fk_stock_operation_reversed_by", "stock_operation", type_="foreignkey")
    op.drop_constraint("fk_stock_operation_reverses", "stock_operation", type_="foreignkey")
    op.drop_constraint("fk_stock_operation_document", "stock_operation", type_="foreignkey")
    op.drop_column("stock_operation", "reversed_by_id")
    op.drop_column("stock_operation", "reverses_id")
    op.drop_column("stock_operation", "source_document_id")
