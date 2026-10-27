"""One sign-in may request separately reviewed access to several branches."""
from alembic import op
import sqlalchemy as sa

revision = 'v136_branch_reg_profiles'
down_revision = 'v135_deon_krugersdorp_profile'
branch_labels = None
depends_on = None


def upgrade():
    constraints = sa.inspect(op.get_bind()).get_unique_constraints('franchise_registrations')
    with op.batch_alter_table('franchise_registrations', naming_convention={'uq': 'uq_%(table_name)s_%(column_0_name)s'}) as batch:
        for item in constraints:
            if item['column_names'] == ['email']:
                batch.drop_constraint(item['name'] or 'uq_franchise_registrations_email', type_='unique')
        batch.create_unique_constraint('uq_registration_email_business', ['email', 'business_name'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT email FROM franchise_registrations GROUP BY email HAVING COUNT(*) > 1 LIMIT 1')).first():
        raise RuntimeError('Multiple branch applications exist. Keep their records or resolve them before downgrading.')
    with op.batch_alter_table('franchise_registrations') as batch:
        batch.drop_constraint('uq_registration_email_business', type_='unique')
        batch.create_unique_constraint('uq_franchise_registrations_email', ['email'])
