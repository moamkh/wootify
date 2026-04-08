"""Add current_group_id to enterprise_bale_users for group-based manual selection."""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0007_add_current_group_id'
down_revision = '0006_add_enterprise_manual_groups'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not support ALTER TABLE ADD CONSTRAINT directly.
    # Use batch mode so Alembic can recreate the table with the FK.
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('enterprise_bale_users', recreate='always') as batch_op:
            batch_op.add_column(sa.Column('current_group_id', sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                'fk_enterprise_bale_users_current_group_id',
                'enterprise_manual_groups',
                ['current_group_id'],
                ['id'],
                ondelete='SET NULL',
            )
            batch_op.create_index('ix_enterprise_bale_users_current_group_id', ['current_group_id'])
        return

    op.add_column('enterprise_bale_users', sa.Column('current_group_id', sa.String(36), nullable=True))
    op.create_foreign_key(
        'fk_enterprise_bale_users_current_group_id',
        'enterprise_bale_users',
        'enterprise_manual_groups',
        ['current_group_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_enterprise_bale_users_current_group_id', 'enterprise_bale_users', ['current_group_id'])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('enterprise_bale_users', recreate='always') as batch_op:
            batch_op.drop_index('ix_enterprise_bale_users_current_group_id')
            batch_op.drop_constraint('fk_enterprise_bale_users_current_group_id', type_='foreignkey')
            batch_op.drop_column('current_group_id')
        return

    op.drop_index('ix_enterprise_bale_users_current_group_id', table_name='enterprise_bale_users')
    op.drop_constraint('fk_enterprise_bale_users_current_group_id', 'enterprise_bale_users', type_='foreignkey')
    op.drop_column('enterprise_bale_users', 'current_group_id')
