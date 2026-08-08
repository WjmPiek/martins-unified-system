# Martins Recovery Copy

This is a separate recovery build. It does not replace the current Martins
system or its database.

## Recovered in the isolated database

- Royalty figures and calculations, including June 2026
- Existing grouped franchise links
- Claims policy summaries, claims summaries, client policy detail records,
  claim cases, notes, mappings, and import history
- Heat Map records from the original Heat Map backup
- Manuals already present in the royalty backup

## Kept unchanged intentionally

The Attendance backup contained no employee, manager, or attendance-event
records. The recovery database therefore keeps the attendance records already
present in the royalty backup instead of replacing them with empty data.

## Before making this live

1. Open the recovery build against the isolated recovery database.
2. Confirm June royalties, grouped franchises, Claims, Heat Map, and Manuals.
3. Confirm Attendance records and staff ID card storage separately.
4. Only then take a fresh backup and use this database for deployment.

Do not copy an old `.env` file over this recovery build. Use the new database
connection details in the environment used for testing or deployment.
