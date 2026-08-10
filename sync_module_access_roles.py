from __future__ import annotations

import argparse

from app import create_app
from app.extensions import db
from app.models import Permission, Role, User


MODULE_ACCESS = {
    "Claims Access": {
        "module": "Insurance Claims",
        "description": "Allows this account to open and use the Claims module.",
    },
    "Heat Map Access": {
        "module": "Heat Map",
        "description": "Allows this account to open and use the Heat Map module.",
    },
    "Attendance Access": {
        "module": "Attendance",
        "description": "Allows this account to open and use the Attendance module.",
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--grant-existing",
        action="store_true",
        help="Grant all module access roles to existing franchise users.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        franchise_role = Role.query.filter_by(name="Franchise User").first()
        if franchise_role is None:
            raise SystemExit("Franchise User role was not found.")

        permissions_by_module = {}
        for permission in Permission.query.all():
            permissions_by_module.setdefault(permission.module, []).append(permission)

        access_roles = []
        for role_name, settings in MODULE_ACCESS.items():
            role = Role.query.filter_by(name=role_name).first()
            if role is None:
                role = Role(name=role_name, description=settings["description"])
                db.session.add(role)
            else:
                role.description = settings["description"]

            role.permissions = list(permissions_by_module.get(settings["module"], []))
            access_roles.append(role)

        restricted_modules = {settings["module"] for settings in MODULE_ACCESS.values()}
        franchise_role.permissions = [
            permission
            for permission in franchise_role.permissions
            if permission.module not in restricted_modules
        ]

        if args.grant_existing:
            users = User.query.filter(User.roles.any(Role.name == "Franchise User")).all()
            for user in users:
                for role in access_roles:
                    if role not in user.roles:
                        user.roles.append(role)

        db.session.commit()
        print("Module access roles are ready and broad Franchise User access was removed.")
        if args.grant_existing:
            print("All existing franchise users were granted all three module roles.")


if __name__ == "__main__":
    main()
