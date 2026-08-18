"""Make Manuals compulsory for Franchise Users.

Revision ID: v123_mandatory_franchise_manuals
Revises: v122_minimum_royalty_none
"""
from alembic import op
import sqlalchemy as sa


revision = "v123_mandatory_franchise_manuals"
down_revision = "v122_minimum_royalty_none"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "user_module_access" not in sa.inspect(bind).get_table_names():
        return

    bind.execute(sa.text("""
        UPDATE user_module_access
        SET is_enabled = :enabled
        WHERE module_code = 'manuals:view'
          AND user_id IN (
              SELECT ur.user_id
              FROM user_roles ur
              JOIN roles r ON r.id = ur.role_id
              WHERE r.name = 'Franchise User'
          )
    """), {"enabled": True})
    bind.execute(sa.text("""
        INSERT INTO user_module_access (user_id, module_code, is_enabled, updated_at)
        SELECT DISTINCT ur.user_id, 'manuals:view', :enabled, CURRENT_TIMESTAMP
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE r.name = 'Franchise User'
          AND NOT EXISTS (
              SELECT 1
              FROM user_module_access uma
              WHERE uma.user_id = ur.user_id
                AND uma.module_code = 'manuals:view'
          )
    """), {"enabled": True})


def downgrade():
    # Keep existing rows because they may have been enabled intentionally
    # before Manuals became compulsory.
    pass
