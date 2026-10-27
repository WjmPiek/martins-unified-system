"""Apply the approved Wjm owner profiles, retaining the protected Admin login."""
from alembic import op
import sqlalchemy as sa

revision = 'v137_wjm_owner_profiles'
down_revision = 'v136_branch_reg_profiles'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    identity = connection.execute(sa.text("SELECT id FROM users WHERE lower(email)='wjm@martinsdirect.com' AND is_active=true AND is_active_account=true")).scalar()
    if identity is None:
        return
    for code in ('NORTHCLIFF', 'DAVEYTON', 'BENONI'):
        rows = connection.execute(sa.text("""
            SELECT f.id AS franchise_id,u.id AS owner_id FROM franchises f
            JOIN user_franchises uf ON uf.franchise_id=f.id AND uf.is_primary=true
            JOIN users u ON u.id=uf.user_id JOIN user_roles ur ON ur.user_id=u.id
            JOIN roles r ON r.id=ur.role_id
            WHERE upper(f.franchise_code)=:code AND f.is_performance_active=true
              AND r.name='Franchise User' AND u.is_active=true AND u.is_active_account=true
        """), dict(code=code)).mappings().all()
        if len(rows) != 1:
            raise RuntimeError(f'Verify the active {code} primary owner before applying the approved Wjm profile.')
        row = rows[0]
        existing = connection.execute(sa.text('SELECT id FROM user_access_profiles WHERE identity_user_id=:identity AND franchise_id=:branch'),
                                      dict(identity=identity, branch=row['franchise_id'])).scalar()
        if existing is None:
            connection.execute(sa.text('INSERT INTO user_access_profiles (identity_user_id,principal_user_id,franchise_id,is_active,created_by_id) VALUES (:identity,:owner,:branch,true,:identity)'),
                               dict(identity=identity, owner=row['owner_id'], branch=row['franchise_id']))


def downgrade():
    pass
