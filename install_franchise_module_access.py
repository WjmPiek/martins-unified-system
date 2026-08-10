from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


ACCESS_ROLE_NAMES = '"Franchise User", "Claims Access", "Heat Map Access", "Attendance Access"'


def replace_once(path: Path, old: str, new: str, marker: str) -> bool:
    source = path.read_text(encoding="utf-8")
    if marker in source:
        return False
    if old not in source:
        raise RuntimeError(f"Could not find the expected code in {path}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python install_franchise_module_access.py "C:\\path\\to\\martins-funeral-system"')

    project_root = Path(sys.argv[1]).expanduser().resolve()
    admin_routes = project_root / "app" / "admin" / "routes.py"
    auth_routes = project_root / "app" / "auth" / "routes.py"
    bundled_sync = Path(__file__).with_name("sync_module_access_roles.py")

    for required_path in (admin_routes, auth_routes, bundled_sync):
        if not required_path.exists():
            raise SystemExit(f"Required file not found: {required_path}")

    backup_root = project_root / f"franchise-module-access-backup-{datetime.now():%Y%m%d-%H%M%S}"
    backup_root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(admin_routes, backup_root / "admin-routes.py")
    shutil.copy2(auth_routes, backup_root / "auth-routes.py")

    copied_sync = project_root / "sync_module_access_roles.py"
    if copied_sync.exists():
        shutil.copy2(copied_sync, backup_root / "sync_module_access_roles.py")

    replace_once(
        admin_routes,
        'roles=Role.query.filter(Role.name.in_(["Franchise User"])).order_by(Role.name).all(),',
        """roles=Role.query.filter(
            Role.name.in_(["Franchise User", "Claims Access", "Heat Map Access", "Attendance Access"])
        ).order_by(Role.name).all(),""",
        "Claims Access\", \"Heat Map Access\", \"Attendance Access\"",
    )

    replace_once(
        admin_routes,
        """    user.set_password(password)
    user.roles.append(role)

    ok, message = normalise_user_scope_for_role(user, role.name, franchise_ids)
""",
        """    user.set_password(password)
    user.roles.append(role)
    if role.name == "Franchise User":
        for access_role_name in ("Claims Access", "Heat Map Access", "Attendance Access"):
            access_role = Role.query.filter_by(name=access_role_name).first()
            if access_role and access_role not in user.roles:
                user.roles.append(access_role)

    ok, message = normalise_user_scope_for_role(user, role.name, franchise_ids)
""",
        'for access_role_name in ("Claims Access", "Heat Map Access", "Attendance Access")',
    )

    replace_once(
        auth_routes,
        """        user.roles.append(role)

        db.session.add(user)
""",
        """        user.roles.append(role)
        if default_role_name == "Franchise User":
            for access_role_name in ("Claims Access", "Heat Map Access", "Attendance Access"):
                access_role = Role.query.filter_by(name=access_role_name).first()
                if access_role and access_role not in user.roles:
                    user.roles.append(access_role)

        db.session.add(user)
""",
        'for access_role_name in ("Claims Access", "Heat Map Access", "Attendance Access")',
    )

    shutil.copy2(bundled_sync, copied_sync)

    print("Franchise module access controls installed.")
    print(f"Backup created: {backup_root}")
    print("Next steps:")
    print(f'1. cd "{project_root}"')
    print("2. python sync_module_access_roles.py --grant-existing")
    print("3. Restart the Martins system.")
    print("Then use Admin > Franchise Users > Edit to tick or untick Claims Access, Heat Map Access, and Attendance Access.")


if __name__ == "__main__":
    main()
