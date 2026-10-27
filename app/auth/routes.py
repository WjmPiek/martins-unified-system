from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session, g, abort
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
    if (current_user.has_role('Finance Assistant') or current_user.has_role('Finance Manager')) and current_user.has_permission('dashboard:view'):
        return url_for('admin.imports_data')
    if current_user.has_permission("performance:view"):
        return url_for("performance.graphs")
    if current_user.has_permission("dashboard:view"):
        return url_for("dashboard.index")
    return url_for("franchise.details")


def send_password_reset_email(user):
    token = user.get_reset_token()
    reset_url = url_for("auth.reset_password", token=token, _external=True)
    subject = "Reset your password"
    body = f"""Hello {user.name},

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

    from app.access_profiles import active, choices
    error = None
    if request.method == 'POST' and request.form.get('profile_key'):
        pending = session.pop('profile_login', {})
        identity = db.session.get(User, pending.get('user_id')) if pending.get('user_id') else None
        if (active(identity) and pending.get('token')
                and 0 <= time.time() - pending.get('created', 0) < 300
                and secrets.compare_digest(pending['token'], request.form.get('csrf_token', ''))
                and identity.updated_at.isoformat() == pending.get('updated')):
            return complete_login(identity, request.form['profile_key'])
        error = 'Your sign-in selection expired. Please enter your email and password again.'
    elif request.method == "POST":
        session.pop('profile_login', None)
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.email) == email).first()

        if active(user) and user.check_password(password):
            available = choices(user)
            if len(available) > 1:
                token = secrets.token_urlsafe(32)
                session['profile_login'] = dict(user_id=user.id, updated=user.updated_at.isoformat(), created=time.time(), token=token)
                return render_template('auth/profiles.html', profiles=available, csrf_token=token, signing_in=True), 200, {'Cache-Control': 'no-store'}
            return complete_login(user, available[0]['key'] if available else None)
        error = 'Unable to sign in. Check your email and password, or contact Head Office if your account is inactive.'
    else:
        session.pop('profile_login', None)
    return render_template("auth/login.html", error=error), 200, {'Cache-Control': 'no-store'}


def complete_login(identity, key):
    from app.access_profiles import clear_context, resolve
    effective = resolve(identity, key) if key else identity
    if effective is None:
        abort(403, 'That access profile is unavailable. Sign in again or contact Head Office.')
    clear_context()
    identity.last_login_at = datetime.now(timezone.utc)
    identity.ensure_protected_admin_role()
    db.session.commit()
    login_user(identity)
    if key:
        session['access_profile_key'] = key
    g._login_user = effective
    from app.audit import log_action
    log_action('Access', 'Signed in', f'Login identity #{identity.id}; profile {key or "default"}')
    db.session.commit()
    return redirect(default_landing_url())


@auth_bp.route('/profiles', methods=['GET', 'POST'])
@login_required
def profiles():
    from app.access_profiles import choices, clear_context, resolve
    from app.registration import csrf_token, check_csrf
    user = current_user._get_current_object()
    identity = getattr(user, 'identity_account', user)
    if request.method == 'POST':
        check_csrf()
        key = request.form.get('profile_key', '')
        effective = resolve(identity, key)
        if effective is None:
            abort(403)
        clear_context()
        session['access_profile_key'] = key
        g._login_user = effective
        from app.audit import log_action
        log_action('Access', 'Switched profile', f'Login identity #{identity.id}; profile {key}')
        db.session.commit()
        return redirect(default_landing_url())
    return render_template('auth/profiles.html', profiles=choices(identity), csrf_token=csrf_token(), signing_in=False), 200, {'Cache-Control': 'no-store'}


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(default_landing_url())

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            sent, debug_url = send_password_reset_email(user)
            if sent:
                flash("Password reset instructions have been sent to your email.", "success")
            else:
                flash("Email sending is not configured yet. In development, check the terminal for the reset link.", "warning")
                if debug_url:
                    flash(f"Development reset link: {debug_url}", "info")
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
