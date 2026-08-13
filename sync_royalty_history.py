"""Safely restore monthly royalty figures from the previous royalty database.

This script only writes to ``monthly_figures`` and clears dependent analytics
cache rows.  It deliberately does not touch users, franchise links, attendance,
claims, manuals, heat-map records, or any other module data.

Set these variables before running it:
    DATABASE_URL                 Unified Martins database (normally already in .env)
    ROYALTY_SOURCE_DATABASE_URL  Previous Martins royalty database

Run a preview first:
    python sync_royalty_history.py --dry-run
Then perform the sync:
    python sync_royalty_history.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv


# Make the script behave like the Flask application: read DATABASE_URL from
# the project's existing .env file before checking the process environment.
load_dotenv()

TARGET_URL = os.environ.get("DATABASE_URL", "").strip()
SOURCE_URL = os.environ.get("ROYALTY_SOURCE_DATABASE_URL", "").strip()
SOURCE_FRANCHISE_COLUMNS = ("id", "business_name", "franchise_code", "master_import_id")
MATCH_COLUMNS = ("master_import_id", "franchise_code", "business_name")
EXCLUDED_FIGURE_COLUMNS = {"id", "franchise_id", "created_by_id", "created_at", "updated_at"}


def normalise(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def connect(url):
    return psycopg2.connect(url, cursor_factory=RealDictCursor)


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [row["column_name"] for row in cur.fetchall()]


def require_table(conn, table):
    if not table_columns(conn, table):
        raise RuntimeError(f"Required table is missing: {table}")


def branch_lookup(conn):
    columns = set(table_columns(conn, "franchises"))
    selected = [column for column in SOURCE_FRANCHISE_COLUMNS if column in columns]
    if "id" not in selected or "business_name" not in selected:
        raise RuntimeError("The franchises table does not contain the required id and business_name fields.")
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(selected)} FROM franchises")
        rows = cur.fetchall()

    lookup = {column: {} for column in MATCH_COLUMNS}
    for row in rows:
        for column in MATCH_COLUMNS:
            key = normalise(row.get(column))
            if key:
                lookup[column].setdefault(key, []).append(row)
    return lookup, rows


def map_source_branches(source, target):
    target_lookup, _ = branch_lookup(target)
    source_columns = set(table_columns(source, "franchises"))
    selected = [column for column in SOURCE_FRANCHISE_COLUMNS if column in source_columns]
    with source.cursor() as cur:
        cur.execute(f"SELECT {', '.join(selected)} FROM franchises")
        source_branches = cur.fetchall()

    mappings = {}
    unresolved = []
    ambiguous = []
    for source_branch in source_branches:
        matches = []
        for column in MATCH_COLUMNS:
            key = normalise(source_branch.get(column))
            if key:
                matches = target_lookup[column].get(key, [])
            if len(matches) == 1:
                mappings[source_branch["id"]] = matches[0]["id"]
                break
        else:
            label = source_branch.get("business_name") or f"source franchise {source_branch['id']}"
            if matches:
                ambiguous.append(label)
            else:
                unresolved.append(label)
    return mappings, unresolved, ambiguous


def source_rows(source, source_columns):
    with source.cursor(name="royalty_history_source", cursor_factory=RealDictCursor) as cur:
        cur.itersize = 500
        cur.execute(
            f"SELECT {', '.join(source_columns)} FROM monthly_figures "
            "WHERE year <= EXTRACT(YEAR FROM CURRENT_DATE)::integer "
            "ORDER BY year, month, id"
        )
        while True:
            rows = cur.fetchmany(500)
            if not rows:
                break
            yield from rows


def update_or_insert(target, payload, update_columns, dry_run):
    with target.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM monthly_figures
            WHERE franchise_id = %(franchise_id)s AND year = %(year)s AND month = %(month)s
            ORDER BY id DESC
            LIMIT 1
            """,
            payload,
        )
        existing = cur.fetchone()
        if existing:
            if not dry_run:
                assignments = ", ".join(f"{column} = %({column})s" for column in update_columns)
                assignments += ", updated_at = CURRENT_TIMESTAMP"
                cur.execute(f"UPDATE monthly_figures SET {assignments} WHERE id = %(existing_id)s", {**payload, "existing_id": existing["id"]})
            return "updated"

        if not dry_run:
            columns = ["franchise_id", "year", "month", *update_columns]
            values = ", ".join(f"%({column})s" for column in columns)
            cur.execute(
                f"INSERT INTO monthly_figures ({', '.join(columns)}, created_at, updated_at) "
                f"VALUES ({values}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                payload,
            )
        return "inserted"


def clear_dependent_cache(target, synced_periods, dry_run):
    tables = set()
    with target.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = {row["table_name"] for row in cur.fetchall()}
        if "performance_results" in tables:
            for year, month in synced_periods:
                if not dry_run:
                    cur.execute("DELETE FROM performance_results WHERE year = %s AND month = %s", (year, month))
        if "performance_page_cache" in tables and not dry_run:
            cur.execute("DELETE FROM performance_page_cache")


def main():
    parser = argparse.ArgumentParser(description="Restore monthly royalty history without overwriting other modules.")
    parser.add_argument("--dry-run", action="store_true", help="Preview source rows and branch mapping without writing changes.")
    args = parser.parse_args()

    if not TARGET_URL:
        raise SystemExit("DATABASE_URL is missing. Check the unified system .env file.")
    if not SOURCE_URL:
        raise SystemExit("ROYALTY_SOURCE_DATABASE_URL is missing. Set it to the previous royalty database URL.")

    source = connect(SOURCE_URL)
    target = connect(TARGET_URL)
    try:
        require_table(source, "franchises")
        require_table(source, "monthly_figures")
        require_table(target, "franchises")
        require_table(target, "monthly_figures")

        franchise_map, unresolved, ambiguous = map_source_branches(source, target)
        source_columns = set(table_columns(source, "monthly_figures"))
        target_columns = set(table_columns(target, "monthly_figures"))
        copy_columns = sorted((source_columns & target_columns) - EXCLUDED_FIGURE_COLUMNS - {"year", "month"})
        if not copy_columns:
            raise RuntimeError("No compatible monthly figure fields were found.")

        print(f"Mapped source franchises: {len(franchise_map)}")
        if unresolved:
            print("Unmapped source franchises (their figures will be skipped): " + ", ".join(sorted(unresolved)[:20]))
        if ambiguous:
            print("Ambiguous source franchises (their figures will be skipped): " + ", ".join(sorted(ambiguous)[:20]))
        print("Reading royalty monthly figures...")

        result = Counter()
        periods = Counter()
        skipped = Counter()
        processed = 0
        for row in source_rows(source, ["id", "franchise_id", "year", "month", *copy_columns]):
            processed += 1
            target_franchise_id = franchise_map.get(row["franchise_id"])
            if not target_franchise_id:
                skipped["unmapped franchise"] += 1
                continue
            if not row.get("year") or not row.get("month"):
                skipped["missing period"] += 1
                continue
            payload = {column: row.get(column) for column in copy_columns}
            payload.update({"franchise_id": target_franchise_id, "year": int(row["year"]), "month": int(row["month"])})
            result[update_or_insert(target, payload, copy_columns, args.dry_run)] += 1
            periods[(payload["year"], payload["month"])] += 1
            if processed % 500 == 0:
                if not args.dry_run:
                    target.commit()
                print(f"  processed {processed} rows")

        synced_periods = sorted(periods)
        clear_dependent_cache(target, synced_periods, args.dry_run)
        if not args.dry_run:
            target.commit()
        print(f"DONE: {result['inserted']} inserted, {result['updated']} updated, {sum(skipped.values())} skipped.")
        print("Periods restored: " + ", ".join(f"{year}-{month:02d} ({count})" for (year, month), count in sorted(periods.items())))
        if skipped:
            print("Skipped: " + ", ".join(f"{reason}={count}" for reason, count in skipped.items()))
        if args.dry_run:
            print("Preview only. Run again without --dry-run to write the figures.")
        else:
            print("Next: warm each restored period with the warm-analytics-cache command before opening graphs.")
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
