from __future__ import annotations

import os
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer

from app.franchise_context import get_accessible_franchises
from app.access_profiles import attendance_holder_for


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


def _business_profile(branch):
    """Map the holder's primary franchise record to Attendance's profile fields."""
    def value(name):
        return str(getattr(branch, name, None) or '').strip()
    registered = value('pty_business_name') or value('ck_business_name') or _franchise_name(branch)
    registration = value('pty_number') if value('pty_business_name') else value('ck_number')
    return dict(franchise_id=branch.id, franchise_name=_franchise_name(branch), business_name=registered,
                trading_as=_franchise_name(branch), business_registration_number=registration,
                vat_number=value('vat_number'), office_address=value('office_address'),
                website=value('website_url'), office_number=value('office_number'),
                twenty_four_hour_number=value('after_hours_number'), contact_number=value('franchisee_cell'))


@attendance_launch_bp.route("/launch/attendance")
@login_required
def launch():
    """Open Attendance with the current Martins user and access scope."""
    accessible = get_accessible_franchises()
    active_branch = getattr(current_user,'profile_franchise',None) or (accessible[0] if len(accessible)==1 else None)
    try:
        holder = attendance_holder_for(current_user,active_branch)
    except ValueError as exc:
        abort(409,description=str(exc))
    if not holder.has_permission("attendance:view"):
        abort(403)

    endpoint = _attendance_endpoint()
    secret = _launch_secret()
    if not endpoint or not secret:
        current_app.logger.error(
            "Attendance launch is missing ATTENDANCE_APP_URL or ATTENDANCE_LAUNCH_SECRET"
        )
        abort(503, description="Attendance launch is not configured.")

    if request.args.get('frame') != '1':
        return render_template('attendance_launch/integrated.html',
                               attendance_frame_url=url_for('attendance_launch.launch', frame='1'),
                               attendance_origin=f'{urlsplit(endpoint).scheme}://{urlsplit(endpoint).netloc}')

    franchise_names = sorted(
        {
            _franchise_name(franchise)
            for franchise in get_accessible_franchises()
            if _franchise_name(franchise)
        }
    )
    principal = holder
    branch_scope = []
    business_profile = None
    if 'franchise user' in _role_names():
        branch_scope = [dict(id=branch.id, name=_franchise_name(branch), vat_number=branch.vat_number or '')
                        for branch in principal.assigned_franchises if _franchise_name(branch)]
        primary = next((branch for branch in principal.assigned_franchises if branch.id == principal.assigned_franchise_id()), None)
        if primary:
            business_profile = _business_profile(primary)
    serializer = URLSafeTimedSerializer(secret, salt="martins-attendance-launch-v1")
    token = serializer.dumps(
        {
            "module": "attendance",
            "email": holder.email,
            "name": _display_name(),
            "is_admin": _is_attendance_admin(),
            "roles": sorted(str(role.name) for role in current_user.roles),
            "franchises": franchise_names,
            "branch_scope": branch_scope,
            "business_profile": business_profile,
            "active_franchise_id": active_branch.id if active_branch else None,
            "primary_franchise_id": principal.assigned_franchise_id() if branch_scope else None,
            "return_url": os.getenv("MARTINS_MAIN_APP_URL", "").strip(),
        }
    )
    response = redirect(f"{endpoint}/?{urlencode({'martins_launch': token, 'embedded': '1', 'parent_origin': request.host_url.rstrip('/')})}")
    response.headers['Cache-Control'] = 'no-store'
    return response
