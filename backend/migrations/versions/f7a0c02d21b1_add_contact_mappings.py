"""Add Wootify-owned contact mappings.

Revision ID: f7a0c02d21b1
Revises: e3a7b4c9d102
"""

from alembic import op
import sqlalchemy as sa

revision = "f7a0c02d21b1"
down_revision = "e3a7b4c9d102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_mappings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("instance_id", sa.String(length=36), sa.ForeignKey("instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform_contact_id", sa.String(length=255), nullable=False),
        sa.Column("platform_contact_type", sa.String(length=32), nullable=True),
        sa.Column("chatwoot_contact_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance_id", "platform_contact_id", name="uq_instance_platform_contact"),
    )
    op.create_index("ix_contact_mappings_instance_id", "contact_mappings", ["instance_id"])
    op.create_index("ix_contact_mappings_instance_chatwoot", "contact_mappings", ["instance_id", "chatwoot_contact_id"])


def downgrade() -> None:
    op.drop_index("ix_contact_mappings_instance_chatwoot", table_name="contact_mappings")
    op.drop_index("ix_contact_mappings_instance_id", table_name="contact_mappings")
    op.drop_table("contact_mappings")
