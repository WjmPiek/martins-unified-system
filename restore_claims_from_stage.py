"""Restore legacy Claims data from a local staging database into recovery.

This script intentionally uses two explicit database URLs so it cannot affect
the live Martins database by accident.  It transfers the Claims summaries and
client-level policy records in batches, matching each legacy franchise name to
the shared Martins franchise record where possible.
"""

import os
import re
from datetime import date, datetime, timezone

import psycopg2
import psycopg2.extras
from psycopg2.extras import Json, execute_values


SOURCE_URL = os.environ.get("CLAIMS_STAGE_DATABASE_URL", "").strip()
TARGET_URL = os.environ.get("RECOVERY_DATABASE_URL", "").strip()
BATCH_SIZE = 2000


def clean_name(value):
    value = (value or "").strip().lower()
    value = re.sub(r"^martins funerals?\s+", "", value)
    value = re.sub(r"^martins\s+", "", value)
    value = re.sub(r"\s+\(f\)\s*$", "", value)
    value = value.replace("mosselbaai", "mossel bay")
    return re.sub(r"\s+", " ", value)


def month_start(value):
    if isinstance(value, datetime):
        return date(value.year, value.month, 1)
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    return date.today().replace(day=1)


def load_franchises(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, business_name, franchise_code, ck_business_name, pty_business_name FROM franchises"
        )
        lookup = {}
        for row in cursor.fetchall():
            for value in row[1:]:
                key = clean_name(value)
                if key:
                    lookup.setdefault(key, row[0])
        return lookup


def map_franchise(lookup, franchise_name):
    key = clean_name(franchise_name)
    return lookup.get(key)


def load_users(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, lower(email) FROM users WHERE email IS NOT NULL")
        return {email: user_id for user_id, email in cursor.fetchall() if email}


def add_legacy_mapping_aliases(source, franchise_lookup):
    """Use the legacy Claims mapping table as aliases for shared franchises."""
    with source.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT source_name, mapped_name FROM franchise_mapping_pg")
        for row in cursor.fetchall():
            franchise_id = map_franchise(franchise_lookup, row["mapped_name"])
            if not franchise_id:
                continue
            franchise_lookup.setdefault(clean_name(row["source_name"]), franchise_id)
            franchise_lookup.setdefault(clean_name(row["mapped_name"]), franchise_id)


def repair_claim_franchise_links(target, franchise_lookup):
    """Link legacy labels already copied into the recovery database."""
    tables = (
        "insurance_policy_monthly_raw",
        "insurance_claims_monthly_raw",
        "insurance_policydata_detail_raw",
    )
    total = 0
    with target.cursor() as cursor:
        for table in tables:
            source_column = "claims_franchise_name" if table == "insurance_claims_monthly_raw" else "franchise_name"
            cursor.execute(
                f"SELECT DISTINCT {source_column} FROM {table} WHERE franchise_id IS NULL AND {source_column} IS NOT NULL"
            )
            for (legacy_name,) in cursor.fetchall():
                franchise_id = map_franchise(franchise_lookup, legacy_name)
                if not franchise_id:
                    continue
                cursor.execute(
                    f"UPDATE {table} SET franchise_id = %s WHERE franchise_id IS NULL AND {source_column} = %s",
                    (franchise_id, legacy_name),
                )
                total += cursor.rowcount
    target.commit()
    print(f"Legacy Claims franchise links repaired: {total}", flush=True)


def restore_monthly_rows(source, target, franchise_lookup):
    with source.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM policy_monthly_raw ORDER BY id")
        policies = cursor.fetchall()
        cursor.execute("SELECT * FROM claims_monthly_raw ORDER BY id")
        claims = cursor.fetchall()

    policy_values = [
        (
            map_franchise(franchise_lookup, row["franchise_name"]), row["franchise_name"],
            month_start(row["import_month"]), row["retail_premium"], row["risk_premium"],
            row["claims"], row["claim_count"], row["claim_paid_franchise"],
            row["claim_paid_client"], row["repudiated_pending"], row["grand_total_claims"],
            row["policy_qty"], row["original_risk_premium"], row["r1_policy_fee"],
            row["underwriter_2_1_fee"], row["risk_after_r1"],
            row["single_monthly_premium_total"], row["current_scenario"] or "100% Claim Ratio",
            str(row["source_file"] or "legacy claims database")[:255],
            row["created_at"] or datetime.now(timezone.utc),
        )
        for row in policies
    ]
    claim_values = [
        (
            map_franchise(franchise_lookup, row["claims_franchise_name"]), row["claim_key"],
            row["claims_franchise_name"], month_start(row["claim_month"]), row["claims_amount"],
            row["claim_count"], row["claim_paid_franchise"], row["claim_paid_client"],
            row["repudiated_pending"], row["grand_total_claims"],
            str(row["source_file"] or "legacy claims database")[:255],
            row["created_at"] or datetime.now(timezone.utc),
        )
        for row in claims
    ]
    with target.cursor() as cursor:
        cursor.execute("DELETE FROM insurance_policy_monthly_raw")
        cursor.execute("DELETE FROM insurance_claims_monthly_raw")
        execute_values(cursor, """
            INSERT INTO insurance_policy_monthly_raw (
                franchise_id, franchise_name, import_month, retail_premium, risk_premium,
                claims, claim_count, claim_paid_franchise, claim_paid_client,
                repudiated_pending, grand_total_claims, policy_qty, original_risk_premium,
                r1_policy_fee, underwriter_2_1_fee, risk_after_r1,
                single_monthly_premium_total, current_scenario, source_file, created_at
            ) VALUES %s
        """, policy_values, page_size=500)
        execute_values(cursor, """
            INSERT INTO insurance_claims_monthly_raw (
                franchise_id, claim_key, claims_franchise_name, claim_month, claims_amount,
                claim_count, claim_paid_franchise, claim_paid_client, repudiated_pending,
                grand_total_claims, source_file, created_at
            ) VALUES %s
        """, claim_values, page_size=500)
    target.commit()
    print(f"Monthly policy summaries restored: {len(policy_values)}", flush=True)
    print(f"Monthly claim summaries restored: {len(claim_values)}", flush=True)


def restore_policy_details(source, target, franchise_lookup):
    read_cursor = source.cursor(name="legacy_policy_details", cursor_factory=psycopg2.extras.RealDictCursor)
    read_cursor.itersize = BATCH_SIZE
    read_cursor.execute("SELECT * FROM policydata_detail_raw ORDER BY id")
    with target.cursor() as cursor:
        cursor.execute("DELETE FROM insurance_policydata_detail_raw")
    target.commit()

    inserted = 0
    now = datetime.now(timezone.utc)
    while True:
        rows = read_cursor.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            raw_data = row["raw_data"]
            if raw_data is not None and not isinstance(raw_data, (dict, list)):
                raw_data = str(raw_data)
            values.append((
                str(row["source_file"] or "legacy policy data")[:255],
                month_start(row["import_month"]), row["row_number"] or row["id"],
                map_franchise(franchise_lookup, row["franchise_name"]), row["franchise_name"],
                row["relation"] or "", bool(row["is_mem"]), row["retail_premium"],
                row["original_risk_premium"], row["mpia"], row["single_premium"],
                row["r1_policy_fee"], row["adv_fund_2_1_fee"], row["risk_after_r1"],
                row["new_risk_premium"], Json(raw_data) if isinstance(raw_data, (dict, list)) else raw_data,
                row["created_at"] or now,
            ))
        with target.cursor() as cursor:
            execute_values(cursor, """
                INSERT INTO insurance_policydata_detail_raw (
                    source_file, import_month, row_number, franchise_id, franchise_name,
                    relation, is_mem, retail_premium, original_risk_premium, mpia,
                    single_premium, r1_policy_fee, adv_fund_2_1_fee, risk_after_r1,
                    new_risk_premium, raw_data, created_at
                ) VALUES %s
            """, values, page_size=BATCH_SIZE)
        target.commit()
        inserted += len(values)
        if inserted % 100000 == 0:
            print(f"Policy details restored: {inserted}", flush=True)
    read_cursor.close()
    print(f"Policy details restored: {inserted}", flush=True)


def restore_claims_workflow(source, target, franchise_lookup, user_lookup):
    """Restore the small workflow tables after the large financial datasets.

    Claim ids are re-linked through the immutable claim reference instead of
    relying on matching integer ids between the two databases.
    """
    with source.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM app_claim_cases ORDER BY id")
        cases = cursor.fetchall()
        cursor.execute("SELECT * FROM app_claim_notes ORDER BY id")
        notes = cursor.fetchall()
        cursor.execute("SELECT * FROM app_claim_attachments ORDER BY id")
        attachments = cursor.fetchall()
        cursor.execute("SELECT * FROM franchise_mapping_pg ORDER BY id")
        mappings = cursor.fetchall()
        cursor.execute("SELECT * FROM import_history ORDER BY id")
        imports = cursor.fetchall()

    case_ids = {}
    with target.cursor() as cursor:
        for row in cases:
            franchise_name = str(row["franchise_name"] or "")[:255]
            email = str(row["created_by_email"] or "").lower()
            cursor.execute(
                """
                INSERT INTO insurance_claim_cases (
                    claim_ref, franchise_id, franchise_name, claimant_name, policy_number,
                    id_number, claim_type, claim_date, date_of_death, claim_amount, status,
                    priority, created_by_id, archived, notes, closed_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (claim_ref) DO UPDATE SET
                    franchise_id = EXCLUDED.franchise_id,
                    franchise_name = EXCLUDED.franchise_name,
                    claimant_name = EXCLUDED.claimant_name,
                    policy_number = EXCLUDED.policy_number,
                    id_number = EXCLUDED.id_number,
                    claim_date = EXCLUDED.claim_date,
                    claim_amount = EXCLUDED.claim_amount,
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    archived = EXCLUDED.archived,
                    notes = EXCLUDED.notes,
                    closed_at = EXCLUDED.closed_at,
                    updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                (
                    str(row["claim_ref"] or f"legacy-claim-{row['id']}")[:120],
                    map_franchise(franchise_lookup, franchise_name), franchise_name,
                    str(row["claimant_name"] or "")[:255], str(row["policy_number"] or "")[:120],
                    str(row["deceased_id_number"] or "")[:80], "Funeral Claim", row["claim_date"],
                    None, row["claim_amount"] or 0, str(row["status"] or "Open")[:60],
                    str(row["priority"] or "Normal")[:40], user_lookup.get(email),
                    bool(row["archived"]), row["description"] or "", row["closed_at"],
                    row["created_at"] or datetime.now(), row["updated_at"] or row["created_at"] or datetime.now(),
                ),
            )
            case_ids[row["id"]] = cursor.fetchone()[0]

        cursor.execute("DELETE FROM insurance_claim_notes")
        for row in notes:
            target_claim_id = case_ids.get(row["claim_id"])
            if not target_claim_id:
                continue
            email = str(row["user_email"] or row["created_by_email"] or "").lower()
            cursor.execute(
                """
                INSERT INTO insurance_claim_notes (claim_id, user_id, user_email, note, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (target_claim_id, user_lookup.get(email), email[:255], row["note_text"] or "", row["created_at"] or datetime.now()),
            )

        # The database stores attachment metadata.  File binaries live in the
        # original service storage and are deliberately not pretended to exist.
        cursor.execute("DELETE FROM insurance_claim_attachments")
        for row in attachments:
            target_claim_id = case_ids.get(row["claim_id"])
            if not target_claim_id:
                continue
            email = str(row["uploaded_by_email"] or "").lower()
            filename = str(row["filename"] or row["original_filename"] or "legacy-attachment")[:255]
            cursor.execute(
                """
                INSERT INTO insurance_claim_attachments (
                    claim_id, filename, stored_filename, file_path, content_type,
                    size_bytes, uploaded_by_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (target_claim_id, filename, str(row["stored_filename"] or filename)[:255],
                 str(row["file_path"] or "")[:600], str(row["content_type"] or "")[:120],
                 min(int(row["file_size"] or 0), 2147483647), user_lookup.get(email),
                 row["created_at"] or datetime.now()),
            )

        for row in mappings:
            source_name = str(row["source_name"] or "")[:255]
            mapped_name = str(row["mapped_name"] or "")[:255]
            if not source_name:
                continue
            cursor.execute(
                """
                INSERT INTO insurance_franchise_mapping (
                    source_name, mapped_name, franchise_id, approved, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_name) DO UPDATE SET
                    mapped_name = EXCLUDED.mapped_name,
                    franchise_id = EXCLUDED.franchise_id,
                    approved = EXCLUDED.approved,
                    updated_at = EXCLUDED.updated_at
                """,
                (source_name, mapped_name, map_franchise(franchise_lookup, mapped_name),
                 bool(row["approved"]), row["created_at"] or datetime.now(), row["updated_at"] or datetime.now()),
            )

        cursor.execute("DELETE FROM insurance_import_history")
        for row in imports:
            months = ", ".join(row["imported_months"] or [])
            cursor.execute(
                """
                INSERT INTO insurance_import_history (
                    import_type, source_file, imported_months, row_count, status, message, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (str(row["import_type"] or "legacy")[:80], str(row["source_file"] or "")[:255],
                 months, row["row_count"] or 0, str(row["status"] or "success")[:50],
                 "Restored from the original Claims backup.", row["created_at"] or datetime.now()),
            )
    target.commit()
    print(f"Claim cases restored: {len(cases)}", flush=True)
    print(f"Claim notes restored: {len(notes)}", flush=True)
    print(f"Claim attachment records restored: {len(attachments)}", flush=True)
    print(f"Claim franchise mappings restored: {len(mappings)}", flush=True)
    print(f"Claims import history restored: {len(imports)}", flush=True)


def main():
    if not SOURCE_URL or not TARGET_URL:
        raise SystemExit("Set CLAIMS_STAGE_DATABASE_URL and RECOVERY_DATABASE_URL first.")
    source = psycopg2.connect(SOURCE_URL)
    target = psycopg2.connect(TARGET_URL)
    try:
        franchise_lookup = load_franchises(target)
        user_lookup = load_users(target)
        add_legacy_mapping_aliases(source, franchise_lookup)
        mode = os.environ.get("CLAIMS_RESTORE_MODE", "all").strip().lower()
        if mode not in {"all", "workflow", "relink"}:
            raise SystemExit("CLAIMS_RESTORE_MODE must be 'all', 'workflow', or 'relink'.")
        if mode == "all":
            restore_monthly_rows(source, target, franchise_lookup)
            restore_policy_details(source, target, franchise_lookup)
        if mode in {"all", "workflow"}:
            restore_claims_workflow(source, target, franchise_lookup, user_lookup)
        repair_claim_franchise_links(target, franchise_lookup)
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
