"""Add indexes for bounded Heat Map viewport reads.

Revision ID: v126_heatmap_viewport
Revises: v125_assign_unscoped_heatmap
"""
from alembic import op


revision = "v126_heatmap_viewport"
down_revision = "v125_assign_unscoped_heatmap"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_heatmap_records_viewport",
        "heatmap_records",
        ["latitude", "longitude"],
        unique=False,
    )
    op.create_index(
        "ix_heatmap_records_franchise_viewport",
        "heatmap_records",
        ["franchise_id", "latitude", "longitude"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_heatmap_records_franchise_viewport", table_name="heatmap_records")
    op.drop_index("ix_heatmap_records_viewport", table_name="heatmap_records")
