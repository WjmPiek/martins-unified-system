from __future__ import annotations

import argparse

from app import create_app
from app.extensions import db
from app.models import Permission, Role, User


MODULE_ACCESS = {
    "Claims Access": {
        "module": "Insurance Claims",
        "description": "Allows a franchise user to use the Insurance Claims module.",
    },
    "Heat Map Access": {
        "module": "Heat Map",
        "description": "Allows a franchise user to use the Heat Map module.",
    },
    "Attendance Access": {
        "module": "Attendance",
        "description": "Allows a franchise user to use the Attendance module.",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the franchise module access roles and assign existing users."
    )
    parser.add_argument(
        "--grant-existing",
        action="store_true",
        help="Give all existing Franchise User accounts access to all three modules.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        franchise_user_role = Role.query.filter_by(name="Franchise User").first()
        if not franchise_user_role:
            raise SystemExit("The Franchise User role was not found. No changes were made.")

        required_modules = {config["module"] for config in MODULE_ACCESS.values()}
        permissions = Permission.query.filter(Permission.module.in_(required_modules)).all()
        permissions_by_module = {module: [] for module in required_modules}
        for permission in permissions:
            permissions_by_module[permission.module].append(permission)

        missing_modules = [
            module for module, module_permissions in permissions_by_module.items() if not module_permissions
        ]
        if missing_modules:
            raise SystemExit(
                "Missing permissions for: " + ", ".join(sorted(missing_modules)) + ". No changes were made."
            )

        access_roles = []
        for role_name, config in MODULE_ACCESS.items():
            role = Role.query.filter_by(name=role_name).first()
            if role is None:
                role = Role(
                    name=role_name,
                    description=config["description"],
                    is_system_role=True,
                )
                db.session.add(role)

            role.description = config["description"]
            role.is_system_role = True
            role.permissions = list(permissions_by_module[config["module"]])
            access_roles.append(role)

        # Access is now controlled by the three dedicated roles, not by the base role.
        franchise_user_role.permissions = [
            permission
            for permission in franchise_user_role.permissions
            if permission.module not in required_modules
        ]
        db.session.flush()

        added_assignments = 0
        affected_users = 0
        if args.grant_existing:
            franchise_users = (
                User.query.join(User.roles)
                .filter(Role.id == franchise_user_role.id)
                .distinct()
                .all()
            )
            for user in franchise_users:
                user_changed = False
                for access_role in access_roles:
                    if access_role not in user.roles:
                        user.roles.append(access_role)
                        added_assignments += 1
                        user_changed = True
                if user_changed:
                    affected_users += 1

        db.session.commit()
        print("Franchise module access roles are ready.")
        if args.grant_existing:
            print(
                f"Existing franchise users kept active: {affected_users}; "
                f"module access assignments added: {added_assignments}."
            )


if __name__ == "__main__":
    main()
