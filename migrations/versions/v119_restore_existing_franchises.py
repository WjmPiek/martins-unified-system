"""Restore the production V119 Alembic revision marker.

Revision ID: v119_restore_existing_franchises
Revises: 16e8f5aef664

The deployed database is already stamped at this revision. The source archive
omitted the corresponding revision file, which prevented Alembic from loading
the migration graph during Render's pre-deploy command. The V119 application
changes use the existing schema, so this recovery revision intentionally has
no DDL operations.
"""


revision = "v119_restore_existing_franchises"
down_revision = "16e8f5aef664"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
