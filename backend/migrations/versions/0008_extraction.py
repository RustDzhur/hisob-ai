"""AI extraction: jobs, results, extracted fields, runtime status + RLS

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "ai_job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["document.id", "document.organization_id"],
            name="fk_ai_job_document",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_ai_job_id_org"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ai_job_status",
        ),
    )
    op.create_index(
        "uq_ai_job_active_document",
        "ai_job",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index("ix_ai_job_org_created", "ai_job", ["organization_id", "created_at"])

    op.create_table(
        "ai_result",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("raw_output", JSONB(), nullable=False),
        sa.Column("matches", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["ai_job.id", "ai_job.organization_id"],
            name="fk_ai_result_job",
        ),
    )
    op.create_table(
        "extracted_field",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("value", sa.Text()),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("evidence", sa.Text()),
        sa.Column("validation", sa.String(10), nullable=False),
        sa.Column("applied", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["ai_job.id", "ai_job.organization_id"],
            name="fk_extracted_field_job",
        ),
        sa.UniqueConstraint("job_id", "path", name="uq_extracted_field_path"),
        sa.CheckConstraint("validation IN ('ok','invalid')", name="ck_extracted_field_validation"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_extracted_field_confidence_range",
        ),
    )
    op.create_index("ix_extracted_field_document", "extracted_field", ["document_id"])

    op.create_table(
        "ai_runtime",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_ai_runtime_single_row"),
    )

    for table in ("ai_job", "ai_result", "extracted_field"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_select ON {table} FOR SELECT USING (organization_id = {CUR_ORG})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
        )
    op.execute(
        f"CREATE POLICY ai_job_update ON ai_job FOR UPDATE "
        f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON ai_job TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON ai_result TO hisob_app")
    op.execute("GRANT SELECT, INSERT ON extracted_field TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ai_runtime TO hisob_app")


def downgrade() -> None:
    for t in ("ai_runtime", "extracted_field", "ai_result", "ai_job"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
