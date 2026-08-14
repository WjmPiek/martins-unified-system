"""Add per-user optional module activation.

Revision ID: v121_franchise_user_modules
Revises: v120_heatmap_density_fields
"""
from alembic import op
import sqlalchemy as sa


revision = "v121_franchise_user_modules"
down_revision = "v120_heatmap_density_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_module_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("module_code", sa.String(length=80), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "module_code", name="uq_user_module_access_user_code"),
    )
    op.create_index("ix_user_module_access_user_id", "user_module_access", ["user_id"], unique=False)


def downgrade():
    op.drop_index("ix_user_module_access_user_id", table_name="user_module_access")
    op.drop_table("user_module_access")
