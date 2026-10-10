"""documents, document files, document lines + RLS + permissions

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

DOC_TYPES = (
    "invoice",
    "esf",
    "waybill",
    "ttn",
    "act",
    "contract",
    "receipt",
    "statement",
    "payment_order",
    "return",
    "other",
)
STATUSES = (
    "RECEIVED",
    "VALIDATING",
    "QUEUED",
    "EXTRACTING",
    "NORMALIZING",
    "MATCHING",
    "NEEDS_REVIEW",
    "APPROVED",
    "POSTED",
    "RECONCILED",
    "ARCHIVED",
    "DUPLICATE",
    "REJECTED",
    "FAILED",
    "CANCELLED",
)
NEW_PERMISSIONS = {
    "documents.read": "Просмотр документов",
    "documents.upload": "Загрузка документов",
    "documents.review": "Проверка и правка документов",
    "documents.approve": "Утверждение документов",
}
# Матрица предложена в docs/07-stage3-plan.md (Q21 ждёт подтверждения).
ROLE_PERMS = {
    "owner": list(NEW_PERMISSIONS),
    "org_admin": list(NEW_PERMISSIONS),
    "accountant": list(NEW_PERMISSIONS),
    "document_operator": ["documents.read", "documents.upload", "documents.review"],
    "sales_purchase_manager": ["documents.read", "documents.upload", "documents.review"],
    "storekeeper": ["documents.read", "documents.upload"],
    "auditor": ["documents.read"],
}


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_unique_constraint("uq_counterparty_id_org", "counterparty", ["id", "organization_id"])

    op.create_table(
        "document",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("number", sa.Text()),
        sa.Column("doc_date", sa.Date()),
        sa.Column("counterparty_id", sa.Uuid()),
        sa.Column("currency", sa.String(3), sa.ForeignKey("currency.code")),
        sa.Column("total_amount", sa.Numeric(20, 4)),
        sa.Column("tax_total", sa.Numeric(20, 4)),
        sa.Column("notes", sa.Text()),
        sa.Column("duplicate_of_id", sa.Uuid(), sa.ForeignKey("document.id")),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_document_id_org"),
        sa.ForeignKeyConstraint(
            ["counterparty_id", "organization_id"],
            ["counterparty.id", "counterparty.organization_id"],
            name="fk_document_counterparty",
        ),
        sa.CheckConstraint(f"doc_type IN ({_in(DOC_TYPES)})", name="ck_document_doc_type"),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_document_status"),
        sa.CheckConstraint(
            "total_amount IS NULL OR total_amount >= 0", name="ck_document_total_non_negative"
        ),
        sa.CheckConstraint(
            "tax_total IS NULL OR tax_total >= 0", name="ck_document_tax_non_negative"
        ),
        sa.CheckConstraint(
            "(status = 'REJECTED') = (rejection_reason IS NOT NULL)",
            name="ck_document_reject_reason",
        ),
        sa.CheckConstraint(
            "(status = 'DUPLICATE') = (duplicate_of_id IS NOT NULL)",
            name="ck_document_duplicate_of",
        ),
    )
    op.create_index("ix_document_org_created", "document", ["organization_id", "created_at"])
    op.create_index("ix_document_org_status", "document", ["organization_id", "status"])
    op.create_index(
        "ix_document_org_cp_number", "document", ["organization_id", "counterparty_id", "number"]
    )

    op.create_table(
        "document_file",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_document_file_document",
        ),
        sa.UniqueConstraint("organization_id", "sha256", name="uq_document_file_sha256"),
        sa.UniqueConstraint("document_id", name="uq_document_file_document"),
    )

    op.create_table(
        "document_line",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid()),
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("unit_of_measure.id")),
        sa.Column("quantity", sa.Numeric(20, 6)),
        sa.Column("unit_price", sa.Numeric(20, 4)),
        sa.Column("discount", sa.Numeric(20, 4)),
        sa.Column("tax_amount", sa.Numeric(20, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_document_line_document",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["product.id", "product.organization_id"],
            name="fk_document_line_product",
        ),
        sa.UniqueConstraint("document_id", "position", name="uq_document_line_position"),
        sa.CheckConstraint(
            "(quantity IS NULL OR quantity >= 0) AND (unit_price IS NULL OR unit_price >= 0) AND "
            "(discount IS NULL OR discount >= 0) AND (tax_amount IS NULL OR tax_amount >= 0) AND "
            "(amount IS NULL OR amount >= 0)",
            name="ck_document_line_non_negative",
        ),
        sa.CheckConstraint(
            "length(btrim(description)) > 0", name="ck_document_line_description_not_blank"
        ),
    )

    for table in ("document", "document_file", "document_line"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_select ON {table} FOR SELECT USING (organization_id = {CUR_ORG})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
        )
    op.execute(
        f"CREATE POLICY document_update ON document FOR UPDATE "
        f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY document_line_delete ON document_line FOR DELETE USING (organization_id = {CUR_ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON document TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON document_file TO hisob_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON document_line TO hisob_app")

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
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'documents.%'")
    op.execute("DELETE FROM permission WHERE code LIKE 'documents.%'")
    for t in ("document_line", "document_file", "document"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.drop_constraint("uq_counterparty_id_org", "counterparty", type_="unique")
