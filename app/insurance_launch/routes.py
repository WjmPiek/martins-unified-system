from __future__ import annotations

import os
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, redirect
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer

from app.franchise_context import get_accessible_franchises


insurance_launch_bp = Blueprint("insurance_launch", __name__)


def _insurance_endpoint() -> str:
    return os.getenv(
        "INSURANCE_APP_URL", "https://insurance.martinssystem.co.za"
    ).strip().rstrip("/")


def _launch_secret() -> str:
    return os.getenv("INSURANCE_LAUNCH_SECRET", "").strip()


def _display_name() -> str:
    return str(getattr(current_user, "full_name", "") or current_user.email).strip()


def _is_admin() -> bool:
    return bool(
        current_user.is_admin_user()
        or current_user.has_permission("franchise_management:view")
    )


@insurance_launch_bp.route("/launch/insurance")
@login_required
def launch():
    """Open the external insurance application for an activated user."""
    if not current_user.has_permission("insurance_app:view"):
        abort(403)

    endpoint = _insurance_endpoint()
    secret = _launch_secret()
    if not endpoint:
        current_app.logger.error(
            "Insurance launch is missing INSURANCE_APP_URL"
        )
        abort(503, description="Insurance application launch is not configured.")
    if not secret:
        # Preserve the existing activation-gated launch until the shared Render
        # signing secret has been configured on both services.  Once present,
        # the same route automatically upgrades to one-click signed SSO.
        current_app.logger.warning(
            "INSURANCE_LAUNCH_SECRET is not configured; using login-page fallback"
        )
        return redirect(f"{endpoint}/")

    franchises = sorted(
        {
            str(getattr(franchise, "business_name", "") or "").strip()
            for franchise in get_accessible_franchises()
            if str(getattr(franchise, "business_name", "") or "").strip()
        }
    )
    token = URLSafeTimedSerializer(
        secret, salt="martins-insurance-launch-v1"
    ).dumps(
        {
            "module": "insurance",
            "email": current_user.email,
            "name": _display_name(),
            "is_admin": _is_admin(),
            "franchises": franchises,
            "return_url": os.getenv("MARTINS_MAIN_APP_URL", "").strip(),
        }
    )
    return redirect(f"{endpoint}/auth/launch?{urlencode({'token': token})}")
