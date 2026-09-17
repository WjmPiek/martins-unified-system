# Heat Map and Graph Performance V126

## Heat Map

- The table endpoint is paginated and returns at most 500 rows.
- Totals and map bounds are calculated in PostgreSQL.
- The map requests only its visible bounding box.
- Wide views use server-side grid aggregation capped at 2,500 cells.
- Individual records are returned only for a selected franchise at zoom 12 or closer, capped at 1,500 by default and 2,500 maximum.
- Browser requests are cancelled when a newer filter or viewport request replaces them.
- Automatic geocoding works in batches of at most 100 unmapped addresses for one selected franchise.
- Composite latitude/longitude indexes support viewport filtering.

## Performance graphs

- Existing graph payloads still use `performance_page_cache`.
- A cache miss reads the prepared `performance_results` table once for all five graph panels.
- The request no longer performs repeated per-month and nested rolling-period queries.
- Rebuilt graph payloads are cached for subsequent requests.
- Private browser caching reduces immediate duplicate requests without sharing user-scoped data.

## Verification

- The complete automated suite passes.
- A bounded-load test covers 1,601 heat-map records and verifies table, detail-pin, and aggregate limits.
- A query-count test verifies that a 12-month cache miss uses one `performance_results` query.
