from flask import abort, g, has_request_context, session
from flask_login import current_user
from app.models import Franchise


def is_privileged_user():
    if not has_request_context() or not current_user or not current_user.is_authenticated:
        return False
    if current_user.is_franchise_scoped_user():
        return False
    if current_user.has_role("Admin") or current_user.has_role("Super Admin") or current_user.has_role("Finance Manager"):
        return True
    return current_user.has_permission("franchise_management:view") or current_user.has_permission("franchise_management:manage")


def is_franchise_locked_user():
    return bool(has_request_context() and current_user and current_user.is_authenticated and current_user.is_franchise_scoped_user())


def get_locked_franchise_id():
    return current_user.assigned_franchise_id() if is_franchise_locked_user() else None


def enforce_franchise_access(franchise_id):
    if not current_user.can_access_franchise(franchise_id):
        abort(403)
    return int(franchise_id)


def get_accessible_franchises():
    # CLI/background commands do not have an authenticated request user.
    # They must pass an explicit franchise scope instead of failing through
    # Flask-Login's current_user proxy.
    if not has_request_context() or not current_user or not current_user.is_authenticated:
        return []
    if has_request_context():
        cached = getattr(g, "accessible_franchises_cache", None)
        if cached is not None:
            return cached
        cached = current_user.accessible_franchises()
        g.accessible_franchises_cache = cached
        return cached
    return current_user.accessible_franchises()


def get_selected_franchise():
    if not has_request_context():
        return None
    franchises = get_accessible_franchises()
    if not franchises:
        return None

    if is_franchise_locked_user():
        selected = franchises[0]
        session["selected_franchise_id"] = selected.id
        session["franchise_view_mode"] = True
        return selected

    selected_id = session.get("selected_franchise_id")
    if selected_id:
        for franchise in franchises:
            if franchise.id == selected_id:
                return franchise

    selected = franchises[0]
    session["selected_franchise_id"] = selected.id
    return selected


def set_selected_franchise(franchise_id, franchise_view_mode=False):
    if not has_request_context() or not current_user or not current_user.is_authenticated:
        return False
    if is_franchise_locked_user() and franchise_id != get_locked_franchise_id():
        return False
    if current_user.can_access_franchise(franchise_id):
        session["selected_franchise_id"] = franchise_id
        if franchise_view_mode:
            session["franchise_view_mode"] = True
        return True
    return False


def is_franchise_view_mode():
    if not has_request_context() or not current_user or not current_user.is_authenticated:
        return False
    if not is_privileged_user():
        return True
    return bool(session.get("franchise_view_mode"))


def exit_franchise_view_mode():
    if has_request_context():
        session.pop("franchise_view_mode", None)
