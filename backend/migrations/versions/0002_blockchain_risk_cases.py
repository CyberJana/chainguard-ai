"""Add blockchain observations and risk/case domain tables.

Revision ID: 0002_domain
Revises: 0001_identity
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_domain"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('analyst', 'investigator', 'supervisor', 'administrator')",
            name="ck_user_invitations_role",
        ),
    )
    op.create_index("ix_user_invitations_organization_id", "user_invitations", ["organization_id"])
    op.create_index("ix_user_invitations_email", "user_invitations", ["email"])
    op.create_index("ix_user_invitations_expires_at", "user_invitations", ["expires_at"])

    op.create_table(
        "blockchain_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("network", sa.String(length=20), nullable=False),
        sa.Column("txid", sa.String(length=128), nullable=False),
        sa.Column("block_height", sa.Integer()),
        sa.Column("block_hash", sa.String(length=128)),
        sa.Column("block_time", sa.DateTime(timezone=True)),
        sa.Column("is_confirmed", sa.Boolean()),
        sa.Column("confirmations", sa.Integer()),
        sa.Column("fee_satoshis", sa.BigInteger()),
        sa.Column("input_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_total_satoshis", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("raw_observation", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("network", "txid", name="uq_transaction_network_txid"),
        sa.CheckConstraint("network IN ('bitcoin')", name="ck_transaction_network"),
    )
    op.create_index("ix_blockchain_transactions_txid", "blockchain_transactions", ["txid"])

    op.create_table(
        "transaction_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("blockchain_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observation_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("transaction_id", "observation_hash", name="uq_transaction_observation_hash"),
    )
    op.create_index("ix_transaction_observations_transaction_id", "transaction_observations", ["transaction_id"])

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("blockchain_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("calibrated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "transaction_id", "model_version", name="uq_risk_org_tx_model"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_risk_score_range"),
        sa.CheckConstraint("category IN ('low', 'medium', 'high', 'critical')", name="ck_risk_category"),
    )
    op.create_index("ix_risk_assessments_organization_id", "risk_assessments", ["organization_id"])
    op.create_index("ix_risk_assessments_transaction_id", "risk_assessments", ["transaction_id"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("blockchain_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("risk_assessment_id", sa.Integer(), sa.ForeignKey("risk_assessments.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("assigned_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("severity IN ('medium', 'high', 'critical')", name="ck_alert_severity"),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'investigating', 'dismissed', 'closed')", name="ck_alert_status"),
    )
    op.create_index("ix_alerts_organization_id", "alerts", ["organization_id"])
    op.create_index("ix_alerts_transaction_id", "alerts", ["transaction_id"])
    op.create_index("ix_alerts_assigned_user_id", "alerts", ["assigned_user_id"])

    op.create_table(
        "investigation_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assigned_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('open', 'in_review', 'closed')", name="ck_case_status"),
    )
    op.create_index("ix_investigation_cases_organization_id", "investigation_cases", ["organization_id"])
    op.create_index("ix_investigation_cases_assigned_user_id", "investigation_cases", ["assigned_user_id"])

    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("investigation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_case_events_organization_id", "case_events", ["organization_id"])
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])

    op.create_table(
        "case_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("investigation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("blockchain_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "transaction_id", name="uq_case_transaction"),
    )
    op.create_index("ix_case_transactions_organization_id", "case_transactions", ["organization_id"])

    op.create_table(
        "analyst_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("investigation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analyst_notes_organization_id", "analyst_notes", ["organization_id"])
    op.create_index("ix_analyst_notes_case_id", "analyst_notes", ["case_id"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("investigation_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=150), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("source_provenance", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_evidence_items_organization_id", "evidence_items", ["organization_id"])
    op.create_index("ix_evidence_items_case_id", "evidence_items", ["case_id"])

    op.create_table(
        "evidence_custody_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_evidence_custody_events_organization_id", "evidence_custody_events", ["organization_id"])
    op.create_index("ix_evidence_custody_events_evidence_id", "evidence_custody_events", ["evidence_id"])


def downgrade() -> None:
    op.drop_table("evidence_custody_events")
    op.drop_table("evidence_items")
    op.drop_table("analyst_notes")
    op.drop_table("case_transactions")
    op.drop_table("case_events")
    op.drop_table("investigation_cases")
    op.drop_table("alerts")
    op.drop_table("risk_assessments")
    op.drop_table("transaction_observations")
    op.drop_table("blockchain_transactions")
    op.drop_table("user_invitations")
