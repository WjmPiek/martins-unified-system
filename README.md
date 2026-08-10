# Martins Attendance Launch Integration

This package keeps the full Attendance application and its sidebar intact. It adds a secure handoff from the Martins System sidebar so a signed-in user opens Attendance already authenticated and with their Martins access.

## Before you start

Back up both projects and their databases. This installer does not delete Attendance data or replace the Attendance pages.

You need these two project folders on the same computer:

- Martins unified system: `martins-funeral-system`
- Attendance system: `attendance-register`

## Install

Open PowerShell in the folder containing this package and run:

```powershell
python install_attendance_launch.py `
  "C:\Users\WjmLabtop\OneDrive\SERVER\martins-unified-system\martins-funeral-system" `
  "C:\Users\WjmLabtop\OneDrive\SERVER\attendance-register"
```

The installer creates dated backup folders inside both projects before changing anything.

## Shared settings

Create one secure shared value:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set the same value in both Render services:

### Martins Unified System

```text
ATTENDANCE_APP_URL=https://attendance.martinssystem.co.za
ATTENDANCE_LAUNCH_SECRET=<the shared value>
MARTINS_MAIN_APP_URL=https://martinssystem.co.za
```

### Attendance backend

```text
ATTENDANCE_LAUNCH_SECRET=<the same shared value>
MARTINS_MAIN_APP_URL=https://martinssystem.co.za
```

### Attendance frontend

```text
VITE_MARTINS_MAIN_APP_URL=https://martinssystem.co.za
```

The Attendance backend should use the central Martins database only after its Attendance tables have been migrated there. Do not point it at a new empty database: that would make existing staff and attendance records appear missing.

## Deploy

Commit and push both projects, then redeploy the Martins service, Attendance backend, and Attendance frontend.

## Test

1. Sign in to Martins System.
2. Click **Attendance** in the Martins sidebar.
3. The complete Attendance application and its own sidebar should open already signed in.
4. Click **Logout** inside Attendance. It returns to Martins System.

Normal direct Attendance logins continue to work as before.
