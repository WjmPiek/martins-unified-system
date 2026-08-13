"""Recovered baseline for the database bundled with this project.

Revision ID: 16e8f5aef664
Revises:
Create Date: 2026-08-13

The supplied database is already stamped at this revision.  The original
revision file was omitted from the archive, so this marker restores Alembic's
revision graph without replaying the baseline schema.
"""

from alembic import op


revision = "16e8f5aef664"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
