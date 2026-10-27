"""Editable monthly module premiums and main-holder subscription requests."""
from datetime import datetime, timezone
from decimal import Decimal
from alembic import op
import sqlalchemy as sa

revision = 'v132_module_catalog'
down_revision = 'v131_module_licensing'
branch_labels = None
depends_on = None


def upgrade():
    plans = op.create_table('module_plans',
        sa.Column('module_code', sa.String(80), primary_key=True),
        sa.Column('monthly_premium', sa.Numeric(12,2), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('monthly_premium >= 0', name='ck_module_plan_price_nonnegative'))
    op.bulk_insert(plans, [dict(module_code=code, monthly_premium=Decimal('40.19'), updated_at=datetime.now(timezone.utc))
                          for code in ('heat_map:view','attendance:view')])
    op.create_table('module_subscription_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('franchise_id', sa.Integer(), sa.ForeignKey('franchises.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_module_subscription_requests_owner_user_id','module_subscription_requests',['owner_user_id'])
    op.create_table('module_subscription_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('request_id', sa.Integer(), sa.ForeignKey('module_subscription_requests.id'), nullable=False),
        sa.Column('module_code', sa.String(80), nullable=False),
        sa.Column('monthly_premium', sa.Numeric(12,2), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.UniqueConstraint('request_id','module_code',name='uq_subscription_request_module'))
    with op.batch_alter_table('module_activation_codes') as batch:
        batch.add_column(sa.Column('request_item_id',sa.Integer(),sa.ForeignKey('module_subscription_items.id',name='fk_activation_subscription_item')))


def downgrade():
    with op.batch_alter_table('module_activation_codes') as batch:
        batch.drop_column('request_item_id')
    op.drop_table('module_subscription_items')
    op.drop_index('ix_module_subscription_requests_owner_user_id',table_name='module_subscription_requests')
    op.drop_table('module_subscription_requests')
    op.drop_table('module_plans')
