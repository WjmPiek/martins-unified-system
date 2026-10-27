from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
import secrets
import time
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from app.extensions import db, mail
from app.models import User, Role, Franchise
from datetime import datetime, timezone


auth_bp = Blueprint("auth", __name__)


def user_role_names(user):
    return {role.name for role in getattr(user, "roles", [])}


def default_landing_url():
    """Use the same performance-first landing experience for every user."""
    if current_user.has_permission("performance:view"):
        return url_for("performance.graphs")
    if current_user.has_permission("dashboard:view"):
        return url_for("dashboard.index")
    return url_for("franchise.details")


def send_password_reset_email(user):
    token = user.get_reset_token()
    reset_url = url_for("auth.reset_password", token=token, _external=True)
    subject = "Reset your password"
    franchises = ", ".join(item.business_name for item in user.accessible_franchises()) if user.is_franchise_scoped_user() else "Head Office"
    body = f"""Hello {user.name},

Account: {user.full_name} — {franchises}

A password reset was requested for your account.

Click this link to create a new password:
{reset_url}

This link expires in 30 minutes.

If you did not request this, you can ignore this email.
"""
    try:
        msg = Message(subject=subject, recipients=[user.email], body=body)
        mail.send(msg)
        return True, None
    except Exception as exc:
        if current_app.debug:
            print("Password reset link:", reset_url)
        return False, reset_url


@auth_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(default_landing_url())
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    from app.registration import submit_registration
    return submit_registration()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(default_landing_url())

    error = None
    if request.method == "POST" and request.form.get("account_id"):
        pending = session.pop("login_accounts", {})
        account_id = request.form.get("account_id", type=int)
        if (time.time() - pending.get("created", 0) < 300
                and secrets.compare_digest(request.form.get("choice_token", ""), pending.get("token", ""))
                and pending.get("token")):
            for item in pending.get("accounts", []):
                if item["id"] == account_id:
                    user = db.session.get(User, account_id)
                    if (user and user.is_active and user.is_active_account
                            and user.updated_at.isoformat() == item["updated"]
                            and (item["verified"] or user.check_password(request.form.get("password", "")))):
                        return complete_login(user)
        error = "Your account selection expired or is invalid. Please log in again."
    elif request.method == "POST":
        session.pop("login_accounts", None)
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        candidates = User.query.filter(db.func.lower(User.email) == email,
            User.is_active.is_(True), User.is_active_account.is_(True)).order_by(User.id).all()
        accounts = [user for user in candidates if user.check_password(password)]
        if len(accounts) == 1 and len(candidates) == 1:
            return complete_login(accounts[0])
        if accounts:
            token = secrets.token_urlsafe(32)
            session["login_accounts"] = {"created": time.time(), "token": token,
                "accounts": [{"id": user.id, "updated": user.updated_at.isoformat(), "verified": user in accounts} for user in candidates]}
            return render_template("auth/choose_account.html", accounts=candidates, choice_token=token), 200, {"Cache-Control": "no-store"}
        error = "Unable to log in. Check your email and password, or contact Head Office."
    else:
        session.pop("login_accounts", None)
    return render_template("auth/login.html", error=error), 200, {"Cache-Control": "no-store"}


def complete_login(user):
    # A previous account's franchise context must never follow a new login.
    session.pop("selected_franchise_id", None)
    session.pop("franchise_view_mode", None)
    session.pop("login_accounts", None)
    user.last_login_at = datetime.now(timezone.utc)
    user.ensure_protected_admin_role()
    db.session.commit()
    login_user(user)
    return redirect(default_landing_url())


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("selected_franchise_id", None)
    session.pop("franchise_view_mode", None)
    session.pop("login_accounts", None)
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(default_landing_url())

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        users = User.query.filter(db.func.lower(User.email) == email, User.is_active.is_(True), User.is_active_account.is_(True)).all()

        if users:
            results = [send_password_reset_email(user) for user in users]
            sent = all(result[0] for result in results)
            if sent:
                flash("Password reset instructions have been sent to your email.", "success")
            else:
                flash("Email sending is not configured yet. In development, check the terminal for the reset link.", "warning")
        else:
            flash("If that email exists, reset instructions will be sent.", "info")

        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(default_landing_url())

    user = User.verify_reset_token(token)
    if not user:
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("auth/reset_password.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("auth/reset_password.html")

        user.set_password(password)
        db.session.commit()
        flash("Your password has been updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html")
