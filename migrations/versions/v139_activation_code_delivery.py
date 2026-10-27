"""Persist encrypted admin-visible codes and holder delivery status."""
from alembic import op
import sqlalchemy as sa

revision = 'v139_activation_code_delivery'
down_revision = 'v138_retire_old_northcliff'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('module_activation_codes') as batch:
        batch.add_column(sa.Column('encrypted_code', sa.Text()))
        batch.add_column(sa.Column('recipient_email', sa.String(255)))
        batch.add_column(sa.Column('email_sent_at', sa.DateTime()))
        batch.add_column(sa.Column('email_last_attempt_at', sa.DateTime()))
        batch.add_column(sa.Column('email_last_error', sa.String(255), nullable=False, server_default=''))
        batch.add_column(sa.Column('email_attempts', sa.Integer(), nullable=False, server_default='0'))
    with op.batch_alter_table('module_subscription_requests') as batch:
        batch.add_column(sa.Column('requested_by_user_id', sa.Integer()))
        batch.create_foreign_key('fk_module_request_signin', 'users', ['requested_by_user_id'], ['id'])


def downgrade():
    with op.batch_alter_table('module_subscription_requests') as batch:
        batch.drop_constraint('fk_module_request_signin', type_='foreignkey')
        batch.drop_column('requested_by_user_id')
    with op.batch_alter_table('module_activation_codes') as batch:
        for column in ('encrypted_code','recipient_email','email_sent_at','email_last_attempt_at','email_last_error','email_attempts'):
            batch.drop_column(column)
