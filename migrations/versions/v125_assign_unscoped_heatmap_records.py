"""Assign unscoped Heat Map imports to their creator's primary franchise.

Revision ID: v125_assign_unscoped_heatmap
Revises: v124_heatmap_client_categories
"""
from alembic import op
import sqlalchemy as sa


revision = "v125_assign_unscoped_heatmap"
down_revision = "v124_heatmap_client_categories"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"heatmap_records", "user_franchises"}.issubset(tables):
        return
    bind.execute(sa.text("""
        UPDATE heatmap_records
        SET franchise_id = (
            SELECT uf.franchise_id
            FROM user_franchises uf
            WHERE uf.user_id = heatmap_records.created_by_id
              AND uf.is_primary = :is_primary
            ORDER BY uf.franchise_id
            LIMIT 1
        )
        WHERE franchise_id IS NULL
          AND created_by_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM user_franchises uf
              WHERE uf.user_id = heatmap_records.created_by_id
                AND uf.is_primary = :is_primary
          )
    """), {"is_primary": True})


def downgrade():
    # Do not remove a valid franchise allocation once repaired.
    pass
