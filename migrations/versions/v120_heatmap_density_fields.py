"""Restore the V120 Heat Map revision marker.

Revision ID: v120_heatmap_density_fields
Revises: v119_restore_existing_franchises

The connected production database is already stamped at this revision. The
V120 migration source was lost when the application root was rolled back, and
the restored V119 application does not depend on additional Heat Map columns.
This marker repairs Alembic's revision graph without replaying schema changes
against the live database.
"""


revision = "v120_heatmap_density_fields"
down_revision = "v119_restore_existing_franchises"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
