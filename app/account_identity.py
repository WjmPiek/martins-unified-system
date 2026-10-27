"""Email sharing is limited to separate franchise owner accounts."""
from app.extensions import db
from app.models import User


def can_share_owner_email(email, franchise_id=None, exclude_user_id=None):
    if email.strip().lower() == "wjm@martinsdirect.com":
        return False
    users = User.query.filter(db.func.lower(User.email) == email.strip().lower()).all()
    for user in users:
        if user.id == exclude_user_id:
            continue
        if not user.has_role("Franchise User") or not user.is_franchise_scoped_user():
            return False
        if franchise_id is not None and user.assigned_franchise_id() == franchise_id:
            return False
    return True
