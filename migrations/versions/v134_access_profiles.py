"""Add explicitly approved franchise profiles to an existing sign-in."""
from alembic import op
import sqlalchemy as sa

revision = 'v134_access_profiles'
down_revision = 'v133_module_subscription_alerts'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_access_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('identity_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('principal_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('franchise_id', sa.Integer(), sa.ForeignKey('franchises.id'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('identity_user_id', 'franchise_id', name='uq_identity_franchise_profile'))
    for column in ('identity_user_id', 'principal_user_id', 'franchise_id'):
        op.create_index('ix_user_access_profiles_' + column, 'user_access_profiles', [column])


def downgrade():
    op.drop_table('user_access_profiles')
