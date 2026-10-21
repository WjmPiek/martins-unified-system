"""Add franchise territory studies without changing existing records or roles."""
from alembic import op
import sqlalchemy as sa

revision = "v128_native_territory"
down_revision = "v127_insurance_app"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("franchise_territories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("franchise_id", sa.Integer(), sa.ForeignKey("franchises.id"), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False))
    op.create_table("territory_valuation_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("territory_id", sa.Integer(), sa.ForeignKey("franchise_territories.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("territory_id", "version", name="uq_territory_snapshot_version"))
    op.create_index("ix_territory_valuation_snapshots_territory_id", "territory_valuation_snapshots", ["territory_id"])


def downgrade():
    # Explicit downgrade removes only the new planning history. Never run against
    # production without a separately approved backup/retention decision.
    op.drop_index("ix_territory_valuation_snapshots_territory_id", table_name="territory_valuation_snapshots")
    op.drop_table("territory_valuation_snapshots")
    op.drop_table("franchise_territories")
