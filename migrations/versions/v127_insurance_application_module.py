"""Add the external Insurance Applications module permission.

Revision ID: v127_insurance_app
Revises: v126_heatmap_viewport
"""
from alembic import op
import sqlalchemy as sa


revision = "v127_insurance_app"
down_revision = "v126_heatmap_viewport"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text("""
        INSERT INTO permissions (module, action, code, label, sort_order)
        SELECT 'Insurance Applications', 'view', 'insurance_app:view',
               'Insurance Applications - View', 65
        WHERE NOT EXISTS (
            SELECT 1 FROM permissions WHERE code = 'insurance_app:view'
        )
    """))
    bind.execute(sa.text("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code = 'insurance_app:view'
        WHERE r.name IN ('Admin', 'Super Admin')
          AND NOT EXISTS (
              SELECT 1 FROM role_permissions rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
    """))


def downgrade():
    # Preserve the permission and assignments to avoid locking users out.
    pass
