"""invitations + RLS

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
# Владение токеном приглашения — достаточное основание видеть/менять ровно эту строку.
TOKEN_MATCH = "encode(token_hash, 'hex') = nullif(current_setting('app.invite_token', true), '')"  # noqa: S105


def upgrade() -> None:
    op.create_table(
        "invitation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("email", CITEXT(), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("role.id"), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
    )
    op.create_index(
        "uq_invitation_pending",
        "invitation",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )

    op.execute("ALTER TABLE invitation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invitation FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY invitation_select ON invitation FOR SELECT "
        f"USING (organization_id = {CUR_ORG} OR {TOKEN_MATCH})"
    )
    op.execute(
        f"CREATE POLICY invitation_insert ON invitation FOR INSERT "
        f"WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY invitation_update ON invitation FOR UPDATE "
        f"USING (organization_id = {CUR_ORG} OR {TOKEN_MATCH}) "
        f"WITH CHECK (organization_id = {CUR_ORG} OR {TOKEN_MATCH})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON invitation TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invitation CASCADE")
