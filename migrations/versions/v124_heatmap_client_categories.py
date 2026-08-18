"""Normalize legacy Heat Map client categories.

Revision ID: v124_heatmap_client_categories
Revises: v123_mandatory_franchise_manuals
"""
from alembic import op
import sqlalchemy as sa


revision = "v124_heatmap_client_categories"
down_revision = "v123_mandatory_franchise_manuals"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "heatmap_records" not in sa.inspect(bind).get_table_names():
        return
    bind.execute(sa.text("""
        UPDATE heatmap_records
        SET relation = 'MAP:insurance_clients'
        WHERE lower(trim(COALESCE(relation, ''))) = 'mem'
    """))

    rows = bind.execute(sa.text("""
        SELECT id, franchise_id, mf_file, deceased_name, deceased_surname, dod,
               full_address, address, city, province, next_of_kin_name,
               next_of_kin_surname, contact_number, relation
        FROM heatmap_records
        ORDER BY id DESC
    """)).mappings().all()

    def cleaned(value):
        return " ".join(str(value or "").strip().split()).casefold()

    def identity(row):
        relation = cleaned(row["relation"])
        record_type = relation.split(":", 1)[1] if relation.startswith("map:") else "deceased"
        mf_file = cleaned(row["mf_file"])
        if mf_file:
            return (row["franchise_id"], record_type, "mf", mf_file)
        named = tuple(cleaned(row[field]) for field in (
            "deceased_name", "deceased_surname", "next_of_kin_name",
            "next_of_kin_surname", "dod",
        ))
        if any(named):
            return (row["franchise_id"], record_type, "details", *named)
        return (
            row["franchise_id"], record_type, "location",
            cleaned(row["full_address"] or row["address"]),
            cleaned(row["city"]), cleaned(row["province"]),
            cleaned(row["contact_number"]),
        )

    seen = set()
    duplicate_ids = []
    for row in rows:
        key = identity(row)
        if key in seen:
            duplicate_ids.append(row["id"])
        else:
            seen.add(key)
    for record_id in duplicate_ids:
        bind.execute(
            sa.text("DELETE FROM heatmap_records WHERE id = :record_id"),
            {"record_id": record_id},
        )


def downgrade():
    # Preserve the explicit category; MAP:insurance_clients may also have been
    # selected intentionally after this migration.
    pass
