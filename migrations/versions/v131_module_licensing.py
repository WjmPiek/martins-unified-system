"""30-day optional-module trials and paid activation codes."""
from datetime import datetime, timedelta, timezone
from alembic import op
import sqlalchemy as sa

revision='v131_module_licensing'
down_revision='v130_franchise_registration'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('users',sa.Column('module_trial_started_at',sa.DateTime()))
    op.add_column('user_module_access',sa.Column('trial_expires_at',sa.DateTime()))
    op.add_column('user_module_access',sa.Column('paid_activated_at',sa.DateTime()))
    op.add_column('franchise_registrations',sa.Column('admin_alert_sent_at',sa.DateTime()))
    op.create_table('module_activation_codes',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('module_code',sa.String(80),nullable=False),
        sa.Column('code_hash',sa.String(64),nullable=False),
        sa.Column('issued_by_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('issued_at',sa.DateTime(),nullable=False),
        sa.Column('redeemed_at',sa.DateTime()),sa.Column('revoked_at',sa.DateTime()),
        sa.Column('payment_reference',sa.String(160),nullable=False),
        sa.UniqueConstraint('code_hash',name='uq_module_activation_code_hash'))
    op.create_index('ix_module_activation_codes_user_id','module_activation_codes',['user_id'])
    connection=op.get_bind()
    started=datetime.now(timezone.utc).replace(tzinfo=None)
    expires=started+timedelta(days=30)
    owners=connection.execute(sa.text("SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.name='Franchise User' AND NOT EXISTS (SELECT 1 FROM user_roles ar JOIN roles a ON a.id=ar.role_id WHERE ar.user_id=u.id AND a.name IN ('Admin','Super Admin'))")).scalars().all()
    for owner in owners:
        connection.execute(sa.text('UPDATE users SET module_trial_started_at=:started WHERE id=:id'),{'started':started,'id':owner})
        for code in ('heat_map:view','attendance:view','insurance_app:view'):
            existing=connection.execute(sa.text('SELECT id FROM user_module_access WHERE user_id=:user AND module_code=:code'),{'user':owner,'code':code}).scalar()
            if existing:
                connection.execute(sa.text('UPDATE user_module_access SET is_enabled=:enabled, trial_expires_at=:expires WHERE id=:id'),{'enabled':True,'expires':expires,'id':existing})
            else:
                connection.execute(sa.text('INSERT INTO user_module_access (user_id,module_code,is_enabled,updated_at,trial_expires_at) VALUES (:user,:code,:enabled,:started,:expires)'),{'user':owner,'code':code,'enabled':True,'started':started,'expires':expires})


def downgrade():
    op.drop_index('ix_module_activation_codes_user_id',table_name='module_activation_codes')
    op.drop_table('module_activation_codes')
    op.drop_column('franchise_registrations','admin_alert_sent_at')
    op.drop_column('user_module_access','paid_activated_at')
    op.drop_column('user_module_access','trial_expires_at')
    op.drop_column('users','module_trial_started_at')
