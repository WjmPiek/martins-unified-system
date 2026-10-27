"""Pending franchise access applications, separate from active users."""
from alembic import op
import sqlalchemy as sa

revision = 'v130_franchise_registration'
down_revision = 'v129_territory_research'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('franchise_registrations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('surname', sa.String(120), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('business_name', sa.String(120), nullable=False),
        sa.Column('phone', sa.String(120), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime()),
        sa.Column('reviewed_by_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('review_note', sa.Text(), nullable=False),
        sa.UniqueConstraint('email', name='uq_franchise_registration_email'))
    op.create_index('ix_franchise_registrations_status', 'franchise_registrations', ['status'])


def downgrade():
    op.drop_index('ix_franchise_registrations_status', table_name='franchise_registrations')
    op.drop_table('franchise_registrations')
