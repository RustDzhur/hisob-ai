"""integration connections, sync runs + RLS + permissions

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

NEW_PERMISSIONS = {
    "integrations.read": "Просмотр интеграций и журнала синхронизаций",
    "integrations.manage": "Включение интеграций и запуск синхронизаций",
}
# Матрица — предложение (R15), переспросить после завершения проекта.
ROLE_PERMS = {
    "owner": ["integrations.read", "integrations.manage"],
    "org_admin": ["integrations.read", "integrations.manage"],
    "accountant": ["integrations.read"],
    "auditor": ["integrations.read"],
}


def _rls(table: str, *, update: bool) -> None:
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


def upgrade() -> None:
    op.create_table(
        "integration_connection",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_cursor", sa.Text()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "provider", name="uq_integration_connection"),
    )
    op.create_table(
        "integration_sync_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("cursor_before", sa.Text()),
        sa.Column("cursor_after", sa.Text()),
        sa.Column("records_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "organization_id", "provider", "request_id", name="uq_integration_sync_request"
        ),
        sa.CheckConstraint("mode IN ('MOCK', 'REAL')", name="ck_integration_sync_mode"),
        sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_integration_sync_status"),
        sa.CheckConstraint("records_count >= 0", name="ck_integration_sync_count"),
    )
    op.create_index(
        "ix_integration_sync_run_org", "integration_sync_run", ["organization_id", "created_at"]
    )
    _rls("integration_connection", update=True)
    _rls("integration_sync_run", update=False)
    op.execute("GRANT SELECT, INSERT, UPDATE ON integration_connection TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON integration_sync_run TO hisob_app")

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
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'integrations.%'")
    op.execute("DELETE FROM permission WHERE code LIKE 'integrations.%'")
    for t in ("integration_sync_run", "integration_connection"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
