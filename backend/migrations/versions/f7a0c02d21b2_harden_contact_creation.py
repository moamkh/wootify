"""Persist inbox-scoped contact creation recovery keys.

Revision ID: f7a0c02d21b2
Revises: f7a0c02d21b1
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a0c02d21b2"
down_revision = "f7a0c02d21b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contact_mappings", sa.Column("chatwoot_scope", sa.String(64), nullable=True))
    op.create_table(
        "contact_creations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("instance_id", sa.String(36), sa.ForeignKey("instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform_contact_id", sa.String(255), nullable=False),
        sa.Column("chatwoot_scope", sa.String(64), nullable=False),
        sa.Column("chatwoot_inbox_id", sa.String(255), nullable=False),
        sa.Column("chatwoot_contact_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance_id", "chatwoot_scope", "platform_contact_id", name="uq_contact_creation_peer_scope"),
    )
    op.create_index("ix_contact_creations_instance_id", "contact_creations", ["instance_id"])


def downgrade() -> None:
    op.drop_index("ix_contact_creations_instance_id", table_name="contact_creations")
    op.drop_table("contact_creations")
    op.drop_column("contact_mappings", "chatwoot_scope")
