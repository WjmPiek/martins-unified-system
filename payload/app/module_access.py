from __future__ import annotations


MODULE_ACCESS_ROLES = {
    "claims": "Claims Access",
    "heat_map": "Heat Map Access",
    "attendance": "Attendance Access",
}

MODULE_PERMISSION_NAMES = {
    "claims": "insurance_claims:view",
    "heat_map": "heat_map:view",
    "attendance": "attendance:view",
}

MODULE_ALIASES = {
    "insurance_claims": "claims",
    "heatmap": "heat_map",
}

ADMIN_ROLE_NAMES = {"admin", "super admin"}


def _role_names(user):
    return {
        str(role.name).strip().lower()
        for role in getattr(user, "roles", [])
        if getattr(role, "name", None)
    }


def normalize_module_key(module_key):
    key = str(module_key or "").strip().lower()
    return MODULE_ALIASES.get(key, key)


def has_module_access(user, module_key):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    role_names = _role_names(user)
    if role_names & ADMIN_ROLE_NAMES:
        return True

    key = normalize_module_key(module_key)
    access_role = MODULE_ACCESS_ROLES.get(key)
    if not access_role:
        return False

    if access_role.lower() in role_names:
        return True

    # Franchise owners are controlled exclusively by the Admin module switches.
    # This prevents the broad Franchise User role from silently restoring access.
    if "franchise user" in role_names:
        return False

    permission_name = MODULE_PERMISSION_NAMES.get(key)
    permission_checker = getattr(user, "has_permission", None)
    return bool(
        permission_name
        and callable(permission_checker)
        and permission_checker(permission_name)
    )
