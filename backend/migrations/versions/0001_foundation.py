"""foundation: identity, tenancy, rbac, audit, idempotency + RLS

Revision ID: 0001
Revises:
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT, JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
CUR_USER = "nullif(current_setting('app.current_user', true), '')::uuid"

# Состав ролей: матрица из docs/05-roadmap.md §5 (предложение, Q8 ждёт подтверждения).
PERMISSIONS = {
    "org.read": "Просмотр организации",
    "org.manage": "Управление организацией",
    "members.read": "Просмотр участников",
    "members.manage": "Управление участниками",
    "audit.read": "Просмотр журнала аудита",
}
ALL = list(PERMISSIONS)
ROLES = {
    "owner": ("Владелец", ALL),
    "org_admin": ("Администратор организации", ALL),
    "accountant": ("Бухгалтер", ["org.read", "members.read"]),
    "storekeeper": ("Кладовщик", ["org.read"]),
    "sales_purchase_manager": ("Менеджер продаж/закупок", ["org.read"]),
    "document_operator": ("Оператор документов", ["org.read"]),
    "auditor": ("Аудитор (только чтение)", ["org.read", "members.read", "audit.read"]),
}


def _ts(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=None if nullable else sa.func.now(),
        nullable=nullable,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _ts("last_login_at", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _ts("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("revoked_at", nullable=True),
        sa.Column("ip", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text()),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        _ts("attempted_at"),
    )
    op.create_index("ix_login_attempt_email_time", "login_attempt", ["email", "attempted_at"])

    op.create_table(
        "role",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.UniqueConstraint("code", name="uq_role_code"),
    )
    op.create_table(
        "permission",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
    )
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("role.id"), primary_key=True),
        sa.Column("permission_code", sa.Text(), sa.ForeignKey("permission.code"), primary_key=True),
    )

    op.create_table(
        "organization",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tin", sa.Text()),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("settings", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("role.id"), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        _ts("joined_at"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_membership_organization_id"),
        sa.CheckConstraint("status IN ('active','suspended')", name="ck_membership_status"),
    )
    op.create_index("ix_membership_user_id", "membership", ["user_id"])

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id")),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text()),
        sa.Column("object_id", sa.Uuid()),
        sa.Column("before", JSONB()),
        sa.Column("after", JSONB()),
        sa.Column("correlation_id", sa.Text()),
        sa.Column("ip", sa.Text()),
        _ts("occurred_at"),
    )
    op.create_index("ix_audit_event_org_time", "audit_event", ["organization_id", "occurred_at"])

    op.create_table(
        "idempotency_key",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", JSONB(), nullable=False),
        _ts("created_at"),
    )

    # --- Справочные данные (не демо): права и системные роли ---
    permission = sa.table(
        "permission", sa.column("code", sa.Text), sa.column("description", sa.Text)
    )
    op.bulk_insert(permission, [{"code": c, "description": d} for c, d in PERMISSIONS.items()])
    role = sa.table(
        "role", sa.column("id", sa.Uuid), sa.column("code", sa.Text), sa.column("name", sa.Text)
    )
    role_perm = sa.table(
        "role_permission", sa.column("role_id", sa.Uuid), sa.column("permission_code", sa.Text)
    )
    role_rows, perm_rows = [], []
    for code, (name, perms) in ROLES.items():
        rid = uuid.uuid4()
        role_rows.append({"id": rid, "code": code, "name": name})
        perm_rows.extend({"role_id": rid, "permission_code": p} for p in perms)
    op.bulk_insert(role, role_rows)
    op.bulk_insert(role_perm, perm_rows)

    # --- Журнал аудита: только добавление ---
    op.execute(
        """
        CREATE FUNCTION audit_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_immutable()
        """
    )

    # --- Tenant isolation: Row-Level Security (ADR-005) ---
    for table in ("membership", "audit_event"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY membership_select ON membership FOR SELECT
        USING (organization_id = {CUR_ORG} OR user_id = {CUR_USER})
        """
    )
    op.execute(
        f"""
        CREATE POLICY membership_insert ON membership FOR INSERT
        WITH CHECK (organization_id = {CUR_ORG})
        """
    )
    op.execute(
        f"""
        CREATE POLICY membership_update ON membership FOR UPDATE
        USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})
        """
    )
    op.execute(
        f"""
        CREATE POLICY audit_select ON audit_event FOR SELECT
        USING (organization_id = {CUR_ORG}
               OR (organization_id IS NULL AND actor_user_id = {CUR_USER}))
        """
    )
    op.execute(
        f"""
        CREATE POLICY audit_insert ON audit_event FOR INSERT
        WITH CHECK (organization_id IS NULL OR organization_id = {CUR_ORG})
        """
    )

    # --- Права роли приложения: минимально необходимые ---
    grants = {
        "users": "SELECT, INSERT, UPDATE",
        "sessions": "SELECT, INSERT, UPDATE",
        "login_attempt": "SELECT, INSERT",
        "role": "SELECT",
        "permission": "SELECT",
        "role_permission": "SELECT",
        "organization": "SELECT, INSERT, UPDATE",
        "membership": "SELECT, INSERT, UPDATE",
        "audit_event": "SELECT, INSERT",
        "idempotency_key": "SELECT, INSERT",
    }
    for table, privs in grants.items():
        op.execute(f"GRANT {privs} ON {table} TO hisob_app")


def downgrade() -> None:
    for table in (
        "idempotency_key",
        "audit_event",
        "membership",
        "organization",
        "role_permission",
        "permission",
        "role",
        "login_attempt",
        "sessions",
        "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS audit_event_immutable()")
