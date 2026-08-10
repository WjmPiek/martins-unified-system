from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


ACCESS_ROLE_NAMES = [
    "Franchise User",
    "Claims Access",
    "Heat Map Access",
    "Attendance Access",
]


def replace_required(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Could not find the expected {label} code.")
    return text.replace(old, new, 1)


def backup_file(source, target_root, backup_root):
    if not source.exists():
        return
    relative = source.relative_to(target_root)
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def patch_app_init(path):
    text = path.read_text(encoding="utf-8-sig")
    text = replace_required(
        text,
        "from flask import Flask, g, request, url_for",
        "from flask import Flask, abort, g, request, url_for",
        "Flask imports",
    )

    if "from flask_login import current_user" not in text:
        marker = "from flask import Flask, abort, g, request, url_for\n"
        text = text.replace(marker, marker + "from flask_login import current_user\n", 1)

    if "from app.module_access import has_module_access" not in text:
        marker = "from app.extensions import"
        index = text.find(marker)
        if index < 0:
            raise RuntimeError("Could not find the extension imports in app/__init__.py.")
        line_end = text.find("\n", index)
        text = (
            text[: line_end + 1]
            + "from app.module_access import has_module_access\n"
            + text[line_end + 1 :]
        )

    if "def enforce_module_access():" not in text:
        marker = "    @app.after_request\n    def record_request_timing"
        guard = '''    @app.before_request
    def enforce_module_access():
        if not current_user.is_authenticated:
            return None

        endpoint = request.endpoint or ""
        protected_modules = (
            (("claims_launch.", "insurance_claims."), "claims"),
            (("attendance_launch.", "attendance."), "attendance"),
            (("heatmap.",), "heat_map"),
        )
        for endpoint_prefixes, module_key in protected_modules:
            if endpoint.startswith(endpoint_prefixes) and not has_module_access(
                current_user, module_key
            ):
                abort(403)
        return None

'''
        if marker not in text:
            raise RuntimeError("Could not find the request timing hook in app/__init__.py.")
        text = text.replace(marker, guard + marker, 1)

    if '"has_module_access": has_module_access,' not in text:
        marker = '            "asset_url": _static_asset_url,\n'
        if marker not in text:
            raise RuntimeError("Could not find the branding context in app/__init__.py.")
        text = text.replace(
            marker,
            marker + '            "has_module_access": has_module_access,\n',
            1,
        )

    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def patch_admin_routes(path):
    text = path.read_text(encoding="utf-8-sig")
    old = 'roles=Role.query.filter(Role.name.in_(["Franchise User"])).order_by(Role.name).all(),'
    new = (
        "roles=Role.query.filter(Role.name.in_("
        + repr(ACCESS_ROLE_NAMES)
        + ")).order_by(Role.name).all(),"
    )
    if new not in text:
        if old not in text:
            raise RuntimeError("Could not find the franchise user role query.")
        text = text.replace(old, new, 1)
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def patch_base_template(path):
    text = path.read_text(encoding="utf-8-sig")
    replacements = {
        "current_user.has_permission('attendance:view')": "has_module_access(current_user, 'attendance')",
        "current_user.has_permission('heat_map:view')": "has_module_access(current_user, 'heat_map')",
        "current_user.has_permission('insurance_claims:view')": "has_module_access(current_user, 'claims')",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if any(old in text for old in replacements):
        raise RuntimeError("One or more broad module checks remain in base.html.")
    path.write_text(text, encoding="utf-8")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python install_module_permissions.py <martins-funeral-system-folder>"
        )

    target = Path(sys.argv[1]).expanduser().resolve()
    required = [
        target / "app" / "__init__.py",
        target / "app" / "admin" / "routes.py",
        target / "app" / "templates" / "base.html",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Required files were not found:\n" + "\n".join(missing))

    package_root = Path(__file__).resolve().parent
    payload_root = package_root / "payload"
    backup_root = target / (
        "module-permission-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )

    files_to_backup = required + [
        target / "app" / "module_access.py",
        target / "sync_module_access_roles.py",
    ]
    for path in files_to_backup:
        backup_file(path, target, backup_root)

    shutil.copy2(payload_root / "app" / "module_access.py", target / "app" / "module_access.py")
    shutil.copy2(
        payload_root / "sync_module_access_roles.py",
        target / "sync_module_access_roles.py",
    )

    patch_app_init(target / "app" / "__init__.py")
    patch_admin_routes(target / "app" / "admin" / "routes.py")
    patch_base_template(target / "app" / "templates" / "base.html")

    print("Franchise module permissions installed.")
    print(f"Backup created: {backup_root}")
    print("Next run: python sync_module_access_roles.py")
    print("Then restart the Martins system and test one franchise account.")


if __name__ == "__main__":
    main()
