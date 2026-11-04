"""Document audience and activation rules, independent of navigation visibility."""
import re
from app.models import FRANCHISE_SCOPED_ROLE_NAMES

OWNER_EMAIL = "wjm@martinsdirect.com"
MODULES = {"attendance:view": "Attendance", "heat_map:view": "Heat Map",
           "insurance_app:view": "Insurance Applications", "insurance_claims:view": "Insurance Claims"}
AUDIENCES = {"all": "All users with Manuals access", "head_office": "Head Office only",
             "selected": "Selected people and the system owner"}


def identity(user):
    return getattr(user, "identity_account", user)


def active(user):
    return bool(user and user.is_authenticated and user.is_active and user.is_active_account)


def is_owner(user):
    account = identity(user)
    return active(account) and (account.email or "").strip().casefold() == OWNER_EMAIL


def head_office(user):
    account = identity(user)
    return active(account) and any(role.name not in FRANCHISE_SCOPED_ROLE_NAMES | {"Franchise"}
                                   for role in account.roles)


def core_module(title):
    title = re.sub(r"\.pdf$", "", (title or "").strip(), flags=re.I).replace("_", " ")
    match = re.fullmatch(r"module\s*([1-7])", title.strip(), flags=re.I)
    return int(match.group(1)) if match else None


def territory(title):
    return "territor" in (title or "").casefold()


def defaults(title):
    text = (title or "").casefold().replace("_", " ")
    code = next((code for word, code in [("attendance", "attendance:view"), ("heat map", "heat_map:view"),
                 ("insurance application", "insurance_app:view"), ("insurance sales", "insurance_app:view"),
                 ("insurance claim", "insurance_claims:view")] if word in text), None)
    audience = "selected" if territory(title) else "head_office" if "administrator" in text else "all"
    return {"audience": audience, "required_module": None if core_module(title) else code}


def can_read(user, manual):
    if not active(identity(user)) or not user.has_permission("manuals:view") or manual.removed_at:
        return False
    if is_owner(user):
        return True
    # Compulsory core documents cannot be hidden by an accidental audience setting.
    if core_module(manual.title):
        return True
    selected = any(reader.id == identity(user).id for reader in manual.readers)
    if territory(manual.title):
        allowed = head_office(user) and selected
    else:
        allowed = (manual.audience == "all" or
                   (manual.audience == "head_office" and head_office(user)) or
                   (manual.audience == "selected" and selected))
    required = defaults(manual.title)["required_module"] or manual.required_module
    return bool(allowed and (not required or user.has_permission(required)))


def can_manage(user):
    return is_owner(user) and user.has_permission("manuals:view") and user.has_permission("manuals:manage")


def description(manual):
    if core_module(manual.title):
        return "Compulsory — all franchise users"
    if territory(manual.title):
        return "Owner and selected Head Office employees"
    label = AUDIENCES.get(manual.audience, "Restricted")
    required = defaults(manual.title)["required_module"] or manual.required_module
    return label + (" — requires " + MODULES.get(required, "module access") if required else "")
