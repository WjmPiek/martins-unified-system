"""Automatic module guides; retain the explicit Head Office Territory audience."""
from alembic import op
import sqlalchemy as sa

revision = "v141_auto_manual_modules"
down_revision = "v140_manual_owner_removal"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("UPDATE mff_manuals SET audience = 'module' WHERE required_module IS NOT NULL AND required_module <> '' AND lower(title) NOT LIKE '%territor%'"))
    op.execute(sa.text("UPDATE mff_manuals SET audience = 'selected', required_module = NULL WHERE lower(title) LIKE '%territor%'"))


def downgrade():
    # Preserve the earlier equivalent module gate for automatic guides.
    op.execute(sa.text("UPDATE mff_manuals SET audience = 'all' WHERE audience = 'module'"))
