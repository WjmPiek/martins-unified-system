# Royalty Data Recovery

This recovery restores monthly royalty figures from the prior royalty database
into the unified Martins database. It does not remove or replace data used by
Attendance, Heat Map, Manuals, Claims, users, franchise links, or groups.

## 1. Stop the local server

Press `Ctrl+C` in the window where `python run.py` is running.

## 2. Set the royalty source once for this PowerShell window

Use the current external connection URL for the renamed `martins_royalty_db`.
Do not paste the words `External Database URL`, only the connection value.

```powershell
$env:ROYALTY_SOURCE_DATABASE_URL="paste the current Martins royalty database URL here"
```

The unified system database continues to come from its existing `.env` file.

## 3. Preview the import

```powershell
python sync_royalty_history.py --dry-run
```

The preview prints the branch mappings and periods it found. Confirm that it
includes `2026-06` and `2026-07`.

## 4. Restore all available monthly figures

```powershell
python sync_royalty_history.py
```

The script restores every monthly period available in the royalty source up to
today. Existing matching branch/month rows are updated; missing rows are added.
It does not delete any data.

## 5. Prepare the graph cache once

Run this for each period listed by the script, for example:

```powershell
flask --app run.py warm-analytics-cache --month 6 --year 2026
flask --app run.py warm-analytics-cache --month 7 --year 2026
```

## 6. Start the system

```powershell
python run.py
```

The royalty overview should show the restored period. Graph pages should use the
prepared cache instead of rebuilding all figures while you browse.
