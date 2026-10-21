"""Add persistent, franchise-scoped public research runs."""
from alembic import op
import sqlalchemy as sa

revision = "v129_territory_research"
down_revision = "v128_native_territory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("territory_research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("franchise_id", sa.Integer(), sa.ForeignKey("franchises.id"), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text()),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("import_jobs.id")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()))
    op.create_index("ix_territory_research_runs_franchise_id", "territory_research_runs", ["franchise_id"])


def downgrade():
    op.drop_index("ix_territory_research_runs_franchise_id", table_name="territory_research_runs")
    op.drop_table("territory_research_runs")
