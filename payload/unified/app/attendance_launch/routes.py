from __future__ import annotations

import os
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, redirect
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer

from app.franchise_context import get_accessible_franchises


attendance_launch_bp = Blueprint("attendance_launch", __name__)


def _attendance_endpoint() -> str:
    return os.getenv("ATTENDANCE_APP_URL", "").strip().rstrip("/")


def _launch_secret() -> str:
    return os.getenv("ATTENDANCE_LAUNCH_SECRET", "").strip()


def _role_names() -> set[str]:
    return {str(role.name).strip().lower() for role in current_user.roles}


def _is_attendance_admin() -> bool:
    return bool(
        current_user.has_permission("franchise_management:view")
        or current_user.has_permission("attendance:manage")
        or {"admin", "super admin", "finance manager", "finance assistant"} & _role_names()
    )


def _display_name() -> str:
    full_name = str(
        getattr(current_user, "full_name", None)
        or getattr(current_user, "name", None)
        or ""
    ).strip()
    if full_name:
        return full_name
    first_name = str(getattr(current_user, "first_name", "") or "").strip()
    last_name = str(getattr(current_user, "last_name", "") or "").strip()
    return " ".join(part for part in (first_name, last_name) if part) or current_user.email


def _franchise_name(franchise) -> str:
    return str(
        getattr(franchise, "business_name", None)
        or getattr(franchise, "name", None)
        or ""
    ).strip()


@attendance_launch_bp.route("/launch/attendance")
@login_required
def launch():
    """Open Attendance with the current Martins user and access scope."""
    endpoint = _attendance_endpoint()
    secret = _launch_secret()
    if not endpoint or not secret:
        current_app.logger.error(
            "Attendance launch is missing ATTENDANCE_APP_URL or ATTENDANCE_LAUNCH_SECRET"
        )
        abort(503, description="Attendance launch is not configured.")

    franchise_names = sorted(
        {
            _franchise_name(franchise)
            for franchise in get_accessible_franchises()
            if _franchise_name(franchise)
        }
    )
    serializer = URLSafeTimedSerializer(secret, salt="martins-attendance-launch-v1")
    token = serializer.dumps(
        {
            "module": "attendance",
            "email": current_user.email,
            "name": _display_name(),
            "is_admin": _is_attendance_admin(),
            "roles": sorted(str(role.name) for role in current_user.roles),
            "franchises": franchise_names,
            "return_url": os.getenv("MARTINS_MAIN_APP_URL", "").strip(),
        }
    )
    return redirect(f"{endpoint}/?{urlencode({'martins_launch': token})}")
