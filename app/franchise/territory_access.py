"""Single-account restriction, additional to existing franchise permissions."""
OWNER_EMAIL = "wjm@martinsdirect.com"


def is_territory_owner(user):
    # Never use is_protected_admin: it also accepts a matching display name.
    return bool(user and user.is_authenticated and user.is_active
                and user.is_active_account
                and (user.email or "").strip().casefold() == OWNER_EMAIL)


def can_view_territories(user):
    return is_territory_owner(user) and user.has_permission("franchise_details:view")
