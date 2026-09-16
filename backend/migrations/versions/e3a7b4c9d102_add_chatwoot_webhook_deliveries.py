"""add durable Chatwoot webhook deliveries

Revision ID: e3a7b4c9d102
Revises: d2e9f8a7c6b5
"""

from alembic import op
import sqlalchemy as sa


revision = "e3a7b4c9d102"
down_revision = "d2e9f8a7c6b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chatwoot_webhook_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("delivery_key", sa.String(length=64), nullable=False),
        sa.Column("instance_key", sa.String(length=128), nullable=False),
        sa.Column("platform_key", sa.String(length=64), nullable=False),
        sa.Column("route_key", sa.String(length=128), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_key"),
    )
    op.create_index("ix_chatwoot_webhook_deliveries_delivery_key", "chatwoot_webhook_deliveries", ["delivery_key"], unique=True)
    op.create_index("ix_chatwoot_webhook_deliveries_instance_key", "chatwoot_webhook_deliveries", ["instance_key"])
    op.create_index("ix_chatwoot_webhook_deliveries_status", "chatwoot_webhook_deliveries", ["status"])
    op.create_index("ix_chatwoot_webhook_deliveries_next_attempt_at", "chatwoot_webhook_deliveries", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_chatwoot_webhook_deliveries_next_attempt_at", table_name="chatwoot_webhook_deliveries")
    op.drop_index("ix_chatwoot_webhook_deliveries_status", table_name="chatwoot_webhook_deliveries")
    op.drop_index("ix_chatwoot_webhook_deliveries_instance_key", table_name="chatwoot_webhook_deliveries")
    op.drop_index("ix_chatwoot_webhook_deliveries_delivery_key", table_name="chatwoot_webhook_deliveries")
    op.drop_table("chatwoot_webhook_deliveries")
