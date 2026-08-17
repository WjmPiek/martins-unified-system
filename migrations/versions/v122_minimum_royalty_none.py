"""Add explicit no-minimum royalty option.

Revision ID: v122_minimum_royalty_none
Revises: v121_franchise_user_modules
"""
from alembic import op
import sqlalchemy as sa


revision = "v122_minimum_royalty_none"
down_revision = "v121_franchise_user_modules"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("franchises")}
    if "minimum_royalty_is_none" not in columns:
        op.add_column(
            "franchises",
            sa.Column(
                "minimum_royalty_is_none",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("franchises")}
    if "minimum_royalty_is_none" in columns:
        op.drop_column("franchises", "minimum_royalty_is_none")
