from __future__ import annotations

import os
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, redirect
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer

from app.franchise_context import get_accessible_franchises


claims_launch_bp = Blueprint("claims_launch", __name__)


def _claims_endpoint() -> str:
    return os.getenv("CLAIMS_APP_URL", "").strip().rstrip("/")


def _claims_signing_secret() -> str:
    return os.getenv("CLAIMS_LAUNCH_SECRET", "").strip()


def _role_names() -> set[str]:
    return {str(role.name).strip().lower() for role in current_user.roles}


def _is_claims_admin() -> bool:
    return bool(
        current_user.has_permission("franchise_management:view")
        or current_user.has_permission("insurance_claims:manage")
        or {"admin", "super admin", "finance manager", "finance assistant"}
        & _role_names()
    )


def _franchise_name(franchise) -> str:
    return str(
        getattr(franchise, "business_name", None)
        or getattr(franchise, "name", None)
        or ""
    ).strip()


def _user_display_name() -> str:
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


@claims_launch_bp.route("/launch/claims")
@login_required
def launch():
    """Open the complete Claims application with the current Martins access."""
    if not current_user.has_permission("insurance_claims:view"):
        abort(403)

    endpoint = _claims_endpoint()
    secret = _claims_signing_secret()
    if not endpoint or not secret:
        current_app.logger.error("Claims launch is missing CLAIMS_APP_URL or CLAIMS_LAUNCH_SECRET")
        abort(503, description="Claims launch is not configured.")

    franchise_names = sorted(
        {
            _franchise_name(franchise)
            for franchise in get_accessible_franchises()
            if _franchise_name(franchise)
        }
    )
    serializer = URLSafeTimedSerializer(secret, salt="martins-claims-launch-v1")
    token = serializer.dumps(
        {
            "module": "claims",
            "email": current_user.email,
            "name": _user_display_name(),
            "is_admin": _is_claims_admin(),
            "franchises": franchise_names,
        }
    )
    return redirect(f"{endpoint}/auth/launch?{urlencode({'token': token})}")
