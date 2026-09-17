from __future__ import annotations

import os

from flask import Blueprint, abort, redirect
from flask_login import current_user, login_required


insurance_launch_bp = Blueprint("insurance_launch", __name__)


def _insurance_endpoint() -> str:
    return os.getenv(
        "INSURANCE_APP_URL", "https://insurance.martinssystem.co.za"
    ).strip().rstrip("/")


@insurance_launch_bp.route("/launch/insurance")
@login_required
def launch():
    """Open the external insurance application for an activated user."""
    if not current_user.has_permission("insurance_app:view"):
        abort(403)

    endpoint = _insurance_endpoint()
    if not endpoint:
        abort(503, description="Insurance application launch is not configured.")
    return redirect(f"{endpoint}/")
