"""two-factor authentication (TOTP) + recovery codes

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_mfa",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_step", sa.BigInteger()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "mfa_recovery_code",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_mfa_recovery_code_user_id", "mfa_recovery_code", ["user_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_mfa TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mfa_recovery_code TO hisob_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mfa_recovery_code CASCADE")
    op.execute("DROP TABLE IF EXISTS user_mfa CASCADE")
