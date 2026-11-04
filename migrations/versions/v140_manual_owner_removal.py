"""Owner-controlled manual audiences, activation gates and retained removals."""
from alembic import op
import sqlalchemy as sa

revision = "v140_manual_owner_removal"
down_revision = "v139_activation_code_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mff_manuals", sa.Column("removed_at", sa.DateTime(), nullable=True))
    op.add_column("mff_manuals", sa.Column("audience", sa.String(24), nullable=False, server_default="all"))
    op.add_column("mff_manuals", sa.Column("required_module", sa.String(80), nullable=True))
    op.create_table("mff_manual_readers",
                    sa.Column("manual_id", sa.Integer(), sa.ForeignKey("mff_manuals.id"), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True))
    manuals = sa.table("mff_manuals", sa.column("id"), sa.column("title"), sa.column("audience"), sa.column("required_module"))
    connection = op.get_bind()
    for row in connection.execute(sa.select(manuals.c.id, manuals.c.title)).mappings():
        text = (row["title"] or "").casefold().replace("_", " ")
        audience = "selected" if "territor" in text else "head_office" if "administrator" in text else "all"
        module = next((code for word, code in [("attendance", "attendance:view"), ("heat map", "heat_map:view"),
                      ("insurance application", "insurance_app:view"), ("insurance sales", "insurance_app:view"),
                      ("insurance claim", "insurance_claims:view")] if word in text), None)
        connection.execute(manuals.update().where(manuals.c.id == row["id"]).values(audience=audience, required_module=module))


def downgrade():
    op.drop_table("mff_manual_readers")
    op.drop_column("mff_manuals", "required_module")
    op.drop_column("mff_manuals", "audience")
    op.drop_column("mff_manuals", "removed_at")
