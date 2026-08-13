"""Restore missing Heat Map records from the local staging backup.

Only records not already present in recovery are inserted.  Franchise ownership
is resolved through the shared user-to-franchise relationship.
"""

import os

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values


SOURCE_URL = os.environ.get("HEATMAP_STAGE_DATABASE_URL", "").strip()
TARGET_URL = os.environ.get("RECOVERY_DATABASE_URL", "").strip()


def main():
    if not SOURCE_URL or not TARGET_URL:
        raise SystemExit("Set HEATMAP_STAGE_DATABASE_URL and RECOVERY_DATABASE_URL first.")

    source = psycopg2.connect(SOURCE_URL)
    target = psycopg2.connect(TARGET_URL)
    try:
        with source.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute('SELECT id, email FROM "user"')
            source_emails = {row["id"]: row["email"].strip().lower() for row in cursor.fetchall()}
            cursor.execute("SELECT * FROM record ORDER BY id")
            source_records = cursor.fetchall()

        with target.cursor() as cursor:
            cursor.execute("""
                SELECT lower(u.email), uf.franchise_id
                FROM users u
                JOIN user_franchises uf ON uf.user_id = u.id
                WHERE uf.is_primary = true
            """)
            franchise_by_email = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT mf_file FROM heatmap_records WHERE mf_file IS NOT NULL")
            existing_files = {row[0] for row in cursor.fetchall()}

        values = []
        for row in source_records:
            if row["mf_file"] in existing_files:
                continue
            franchise_id = franchise_by_email.get(source_emails.get(row["user_id"], ""))
            values.append((
                franchise_id, row["mf_file"], row["deceased_name"], row["deceased_surname"],
                row["dod"], row["address"], row["city"], row["province"], row["country"],
                row["full_address"], row["latitude"], row["longitude"], row["weight"],
                row["next_of_kin_name"], row["next_of_kin_surname"], row["relationship"],
                row["relationship"], row["contact_number"], "legacy heat-map backup",
                None, row["created_at"], row["updated_at"],
            ))

        if values:
            with target.cursor() as cursor:
                execute_values(cursor, """
                    INSERT INTO heatmap_records (
                        franchise_id, mf_file, deceased_name, deceased_surname, dod,
                        address, city, province, country, full_address, latitude, longitude,
                        weight, next_of_kin_name, next_of_kin_surname, relationship, relation,
                        contact_number, source_filename, created_by_id, created_at, updated_at
                    ) VALUES %s
                """, values, page_size=500)
            target.commit()
        print(f"Heat-map records restored: {len(values)}")
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
