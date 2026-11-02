"""Add Ethereum transaction observations.

Revision ID: 0003_ethereum
Revises: 0002_domain
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_ethereum"
down_revision = "0002_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ethereum_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("tx_hash", sa.String(length=66), nullable=False),
        sa.Column("block_number", sa.BigInteger()),
        sa.Column("block_hash", sa.String(length=66)),
        sa.Column("receipt_status", sa.String(length=16)),
        sa.Column("from_address", sa.String(length=42), nullable=False),
        sa.Column("to_address", sa.String(length=42)),
        sa.Column("value_wei", sa.String(length=78), nullable=False),
        sa.Column("gas_limit", sa.BigInteger(), nullable=False),
        sa.Column("gas_used", sa.BigInteger()),
        sa.Column("effective_gas_price_wei", sa.String(length=78)),
        sa.Column("raw_observation", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chain_id", "tx_hash", name="uq_ethereum_chain_tx_hash"),
    )
    op.create_index("ix_ethereum_transactions_tx_hash", "ethereum_transactions", ["tx_hash"])

    op.create_table(
        "ethereum_transaction_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("ethereum_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observation_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("transaction_id", "observation_hash", name="uq_ethereum_observation_hash"),
    )
    op.create_index(
        "ix_ethereum_transaction_observations_transaction_id",
        "ethereum_transaction_observations",
        ["transaction_id"],
    )


def downgrade() -> None:
    op.drop_table("ethereum_transaction_observations")
    op.drop_table("ethereum_transactions")
