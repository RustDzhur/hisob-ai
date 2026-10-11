"""copilot conversations and messages + RLS + permission

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
PERM = (
    "copilot.use",
    "Вопросы AI-помощнику (только чтение; права инструментов проверяются отдельно)",
)
# Матрица — предложение (R16), переспросить после завершения проекта.
ROLES = [
    "owner",
    "org_admin",
    "accountant",
    "storekeeper",
    "sales_purchase_manager",
    "document_operator",
    "auditor",
]


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for cmd, clause in (
        ("SELECT", f"USING (organization_id = {CUR_ORG})"),
        ("INSERT", f"WITH CHECK (organization_id = {CUR_ORG})"),
        (
            "UPDATE",
            f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})",
        ),
    ):
        op.execute(f"CREATE POLICY {table}_{cmd.lower()} ON {table} FOR {cmd} {clause}")


def upgrade() -> None:
    op.create_table(
        "copilot_conversation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_copilot_conv_id_org"),
    )
    op.create_index(
        "ix_copilot_conv_user", "copilot_conversation", ["organization_id", "user_id", "updated_at"]
    )
    op.create_table(
        "copilot_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("locale", sa.String(5), nullable=False, server_default="ru"),
        sa.Column("tool_trace", postgresql.JSONB()),
        sa.Column("provider", sa.String(20)),
        sa.Column("model", sa.String(80)),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("error_code", sa.String(60)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["conversation_id", "organization_id"],
            ["copilot_conversation.id", "copilot_conversation.organization_id"],
            name="fk_copilot_msg_conv",
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_copilot_msg_role"),
        sa.CheckConstraint(
            "status IN ('DONE', 'PENDING', 'RUNNING', 'FAILED')", name="ck_copilot_msg_status"
        ),
    )
    op.create_index(
        "ix_copilot_msg_conv",
        "copilot_message",
        ["organization_id", "conversation_id", "created_at"],
    )
    _rls("copilot_conversation")
    _rls("copilot_message")
    op.execute("GRANT SELECT, INSERT, UPDATE ON copilot_conversation TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON copilot_message TO hisob_app")
    perm = sa.table("permission", sa.column("code", sa.Text), sa.column("description", sa.Text))
    op.bulk_insert(perm, [dict(code=PERM[0], description=PERM[1])])
    for role in ROLES:
        op.execute(
            sa.text(
                "INSERT INTO role_permission (role_id, permission_code) SELECT id, :p FROM role WHERE code = :r"
            ).bindparams(p=PERM[0], r=role)
        )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'copilot.%'")
    op.execute("DELETE FROM permission WHERE code LIKE 'copilot.%'")
    for t in ("copilot_message", "copilot_conversation"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
