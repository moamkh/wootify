"""Track verified additional Chatwoot contacts for a platform peer.

Revision ID: f7a0c02d21b3
Revises: f7a0c02d21b2
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a0c02d21b3"
down_revision = "f7a0c02d21b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("instance_id", sa.String(36), sa.ForeignKey("instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chatwoot_scope", sa.String(64), nullable=False),
        sa.Column("chatwoot_contact_id", sa.String(255), nullable=False),
        sa.Column("platform_contact_id", sa.String(255), nullable=False),
        sa.Column("verification_method", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance_id", "chatwoot_scope", "chatwoot_contact_id", name="uq_contact_alias_scope_contact"),
    )
    op.create_index("ix_contact_aliases_instance_id", "contact_aliases", ["instance_id"])
    op.create_index("ix_contact_aliases_instance_peer", "contact_aliases", ["instance_id", "platform_contact_id"])


def downgrade() -> None:
    op.drop_index("ix_contact_aliases_instance_peer", table_name="contact_aliases")
    op.drop_index("ix_contact_aliases_instance_id", table_name="contact_aliases")
    op.drop_table("contact_aliases")
