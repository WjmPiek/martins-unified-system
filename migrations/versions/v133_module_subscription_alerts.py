"""Track delivery of Head Office module subscription alerts."""
from alembic import op
import sqlalchemy as sa

revision = 'v133_module_subscription_alerts'
down_revision = 'v132_module_catalog'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('module_subscription_requests', sa.Column('admin_alert_sent_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('module_subscription_requests') as batch:
        batch.drop_column('admin_alert_sent_at')
