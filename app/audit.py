from flask_login import current_user
from app.extensions import db
from app.models import AuditLog


def log_action(module, action, details=""):
    """Record important system activity for admin review."""
    try:
        user_id = current_user.id if current_user and current_user.is_authenticated else None
    except Exception:
        user_id = None
    identity = getattr(current_user, 'identity_account', None)
    if identity is not None:
        details = f"{details or ''} [sign-in user #{identity.id}; profile {getattr(current_user, 'profile_key', 'default')}]"
    db.session.add(AuditLog(user_id=user_id, module=module, action=action, details=details or ""))
