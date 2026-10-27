"""Allow separate franchise owner identities to share a login email."""
from alembic import op
import sqlalchemy as sa

revision = 'v134_shared_franchise_email'
down_revision = 'v133_module_subscription_alerts'
branch_labels = None
depends_on = None


def remove_email_uniqueness(table):
    inspector = sa.inspect(op.get_bind())
    constraints = [item for item in inspector.get_unique_constraints(table)
                   if item['column_names'] == ['email']]
    indexes = [item for item in inspector.get_indexes(table)
               if item['column_names'] == ['email'] and item.get('unique')
               and not item.get('duplicates_constraint')]
    with op.batch_alter_table(table, naming_convention={'uq': 'uq_%(table_name)s_%(column_0_name)s'}) as batch:
        for item in constraints:
            batch.drop_constraint(item['name'] or f'uq_{table}_email', type_='unique')
        for item in indexes:
            batch.drop_index(item['name'])
        if table == 'users' and indexes:
            batch.create_index('ix_users_email', ['email'], unique=False)


def upgrade():
    remove_email_uniqueness('users')
    remove_email_uniqueness('franchise_registrations')
    with op.batch_alter_table('franchise_registrations') as batch:
        batch.create_unique_constraint('uq_registration_email_business', ['email', 'business_name'])


def downgrade():
    # Never merge/delete accounts to restore the old constraint.
    for table in ('users', 'franchise_registrations'):
        duplicates = op.get_bind().execute(sa.text(
            f'SELECT email FROM {table} GROUP BY email HAVING COUNT(*) > 1 LIMIT 1')).first()
        if duplicates:
            raise RuntimeError('Resolve shared emails before downgrading; no accounts were removed.')
    with op.batch_alter_table('franchise_registrations') as batch:
        batch.drop_constraint('uq_registration_email_business', type_='unique')
        batch.create_unique_constraint('uq_franchise_registrations_email', ['email'])
    with op.batch_alter_table('users') as batch:
        batch.create_unique_constraint('uq_users_email', ['email'])
