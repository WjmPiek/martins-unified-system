#!/usr/bin/env python3
"""Return an integrated Martins installation to the royalty-only system.

The removed modules are moved into a timestamped backup directory. Database
tables and records are deliberately left untouched so this operation remains
reversible and cannot erase royalty or historical module data.
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


MODULE_PATHS = (
    "app/attendance",
    "app/attendance_launch",
    "app/claims_launch",
    "app/heatmap",
    "app/insurance_claims",
    "app/insurance_claims_seed",
    "app/manuals",
    "app/manuals_seed",
    "app/module_access.py",
    "app/templates/admin/manual_allocate_contact.html",
    "app/templates/attendance",
    "app/templates/heatmap",
    "app/templates/insurance_claims",
    "app/templates/manuals",
    "app/static/js/heatmap.js",
    "claims_recovery_restore_error.log",
    "claims_recovery_restore.log",
    "install_attendance_launch.py",
    "install_claims_launch.py",
    "install_franchise_module_access.py",
    "install_module_permissions.py",
    "merge_claims_database_only.py",
    "merge_old_module_databases.py",
    "restore_claims_from_stage.py",
    "restore_heatmap_from_stage.py",
    "restore_manual_documents.py",
    "sync_module_access_roles.py",
    "payload",
)

MODULE_GLOBS = (
    "attendance-launch-backup-*",
    "claims-launch-backup-*",
    "module-permission-backup-*",
)

SHARED_FILES = (
    "app/__init__.py",
    "app/admin/routes.py",
    "app/templates/base.html",
)

CORE_PATHS = (
    "app/royalties",
    "app/performance",
    "app/monthly",
    "app/franchise",
    "app/__init__.py",
    "app/templates/base.html",
)

MODULE_REFERENCES = (
    "attendance_launch",
    "claims_launch",
    "insurance_claims",
    "heatmap.",
    "manuals.",
    "has_module_access",
    "app.module_access",
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def copy_to_backup(root: Path, backup: Path, relative: str) -> None:
    source = root / relative
    if not source.exists():
        return
    destination = backup / "original-shared-files" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def restore_shared_files(root: Path, backup: Path) -> None:
    for relative in SHARED_FILES:
        source = backup / "original-shared-files" / relative
        if source.exists():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def patch_app_init(path: Path) -> None:
    content = read_text(path)

    content = re.sub(
        r"\n    @app\.before_request\n"
        r"    def enforce_module_access\(\):\n"
        r".*?(?=\n    @app\.|\n    from app\.)",
        "\n",
        content,
        flags=re.DOTALL,
    )

    remove_fragments = (
        "from flask_login import current_user",
        "from app.module_access import has_module_access",
        "from app.heatmap.routes import heatmap_bp",
        "from app.attendance.routes import attendance_bp",
        "from app.manuals.routes import manuals_bp",
        "from app.insurance_claims.routes import insurance_claims_bp",
        "from app.claims_launch.routes import claims_launch_bp",
        "from app.attendance_launch.routes import attendance_launch_bp",
        "app.register_blueprint(heatmap_bp)",
        "app.register_blueprint(attendance_bp)",
        "app.register_blueprint(manuals_bp)",
        "app.register_blueprint(insurance_claims_bp)",
        "app.register_blueprint(claims_launch_bp)",
        "app.register_blueprint(attendance_launch_bp)",
        '"has_module_access": has_module_access,',
        "'has_module_access': has_module_access,",
    )
    lines = [
        line
        for line in content.splitlines()
        if not any(fragment in line for fragment in remove_fragments)
    ]
    content = "\n".join(lines).rstrip() + "\n"
    content = content.replace("from flask import Flask, abort, g, request, url_for", "from flask import Flask, g, request, url_for")
    write_text(path, content)


def patch_admin_routes(path: Path) -> None:
    content = read_text(path)
    content, replacements = re.subn(
        r"roles=Role\.query\.filter\(Role\.name\.in_\(\["
        r"['\"]Franchise User['\"],\s*['\"]Claims Access['\"],\s*"
        r"['\"]Heat Map Access['\"],\s*['\"]Attendance Access['\"]"
        r"\]\)\)\.order_by\(Role\.name\)\.all\(\),",
        "roles=Role.query.filter_by(name='Franchise User').order_by(Role.name).all(),",
        content,
    )
    if replacements == 0:
        content = re.sub(
            r"roles=Role\.query\.filter\(Role\.name\.in_\(\[[^\]]*"
            r"(?:Claims Access|Heat Map Access|Attendance Access)[^\]]*\]\)\)"
            r"\.order_by\(Role\.name\)\.all\(\),",
            "roles=Role.query.filter_by(name='Franchise User').order_by(Role.name).all(),",
            content,
        )
    write_text(path, content)


def remove_jinja_permission_block(content: str, marker: str) -> str:
    pattern = (
        r"\n\s*\{% if [^%]*" + re.escape(marker) + r"[^%]*%\}"
        r".*?\{% endif %\}"
    )
    return re.sub(pattern, "", content, flags=re.DOTALL)


def patch_base_template(path: Path) -> None:
    content = read_text(path)

    # Remove the complete Claims navigation domain before removing remaining
    # single-line module links.
    content = re.sub(
        r"\n\s*\{% if has_module_access\(current_user, ['\"]claims['\"]\) %\}"
        r"\s*<details[^>]*>.*?Insurance Claims.*?</details>\s*"
        r"\{% endif %\}",
        "",
        content,
        flags=re.DOTALL,
    )

    content = re.sub(
        r"\n\s*<div class=[\"']nav-utility-links[\"']>.*?</div>",
        "",
        content,
        flags=re.DOTALL,
    )

    module_markers = (
        "attendance_launch.",
        "attendance.",
        "claims_launch.",
        "insurance_claims.",
        "heatmap.",
        "manuals.",
    )
    lines = []
    for line in content.splitlines():
        if any(marker in line for marker in module_markers):
            continue
        if "has_module_access" in line:
            continue
        lines.append(line)

    content = "\n".join(lines).rstrip() + "\n"
    write_text(path, content)


def verify_shared_files(root: Path) -> None:
    problems: list[str] = []
    for relative in SHARED_FILES:
        path = root / relative
        content = read_text(path)
        for marker in MODULE_REFERENCES:
            if marker in content:
                problems.append(f"{relative}: still contains {marker!r}")

    init_content = read_text(root / "app/__init__.py")
    required_blueprints = ("royalties_bp", "performance_bp", "monthly_bp", "franchise_bp")
    for marker in required_blueprints:
        if marker not in init_content:
            problems.append(f"app/__init__.py: missing core blueprint {marker!r}")

    if problems:
        raise RuntimeError("Shared-file verification failed:\n- " + "\n- ".join(problems))


def move_to_backup(root: Path, backup: Path, relative: Path) -> bool:
    source = root / relative
    if not source.exists():
        return False
    destination = backup / "removed-modules" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"Backup destination already exists: {destination}")
    shutil.move(str(source), str(destination))
    return True


def collect_module_paths(root: Path, backup: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for relative in MODULE_PATHS:
        path = Path(relative)
        if (root / path).exists():
            found[path.as_posix().lower()] = path
    for pattern in MODULE_GLOBS:
        for source in root.glob(pattern):
            if source == backup:
                continue
            relative = source.relative_to(root)
            found[relative.as_posix().lower()] = relative
    return sorted(found.values(), key=lambda item: (len(item.parts), item.as_posix()), reverse=True)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser().resolve()
    missing = [relative for relative in CORE_PATHS if not (root / relative).exists()]
    if missing:
        print("This does not look like the Martins system root.")
        print("Missing: " + ", ".join(missing))
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f"removed-modules-backup-{timestamp}"
    backup.mkdir(parents=True)

    for relative in SHARED_FILES:
        copy_to_backup(root, backup, relative)

    try:
        patch_app_init(root / "app/__init__.py")
        patch_admin_routes(root / "app/admin/routes.py")
        patch_base_template(root / "app/templates/base.html")
        verify_shared_files(root)
    except Exception as exc:
        restore_shared_files(root, backup)
        print(f"Rollback stopped before moving modules: {exc}")
        print(f"Original shared files were restored from {backup}")
        return 1

    moved: list[str] = []
    try:
        for relative in collect_module_paths(root, backup):
            if move_to_backup(root, backup, relative):
                moved.append(relative.as_posix())
    except Exception as exc:
        restore_shared_files(root, backup)
        print(f"Rollback could not finish: {exc}")
        print(f"Original shared files were restored. Module files already moved are in {backup}")
        return 1

    note = root / "ROYALTY_ONLY.txt"
    note.write_text(
        "Martins royalty-only system\n"
        f"Integrated modules were archived on {datetime.now().isoformat(timespec='seconds')}.\n"
        f"Backup folder: {backup.name}\n"
        "No database tables or records were deleted.\n"
        "Core royalty, performance, monthly figures, franchise users, and grouping remain.\n",
        encoding="utf-8",
    )

    print("Royalty-only rollback complete.")
    print(f"Archived {len(moved)} module paths in: {backup}")
    print("No database data was deleted.")
    print("Next: run 'python run.py' and test login, graphs, monthly figures, and royalties.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
