#!/usr/bin/env python3
"""Install the secure Martins-to-Attendance launcher in two existing projects.

Usage:
  python install_attendance_launch.py <martins-unified-system> <attendance-register>
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / "payload"


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def backup(paths: list[Path], root: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = root / f"attendance-launch-backup-{stamp}"
    directory.mkdir(parents=True)
    for path in paths:
        if path.exists():
            target = directory / label / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return directory


def replace_once(path: Path, pattern: str, replacement: str, description: str) -> None:
    text = path.read_text(encoding="utf-8")
    changed, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Could not update {description}: {path}")
    path.write_text(changed, encoding="utf-8")


def add_once(path: Path, marker: str, addition: str, description: str) -> None:
    text = path.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    if marker not in text:
        raise RuntimeError(f"Could not find the insertion point for {description}: {path}")
    path.write_text(text.replace(marker, marker + addition, 1), encoding="utf-8")


def install_unified(unified: Path) -> None:
    app_init = unified / "app" / "__init__.py"
    base = unified / "app" / "templates" / "base.html"
    if not app_init.exists() or not base.exists():
        raise RuntimeError("The Martins unified-system folder does not look correct.")

    target = unified / "app" / "attendance_launch"
    shutil.copytree(PAYLOAD / "unified" / "app" / "attendance_launch", target, dirs_exist_ok=True)

    text = app_init.read_text(encoding="utf-8")
    if "from app.attendance_launch.routes import attendance_launch_bp" not in text:
        claims_import = re.search(
            r"^(?P<indent>\s*)from app\.claims_launch\.routes import claims_launch_bp\s*$",
            text,
            re.MULTILINE,
        )
        if claims_import:
            insert = f"\n{claims_import.group('indent')}from app.attendance_launch.routes import attendance_launch_bp"
            text = text[:claims_import.end()] + insert + text[claims_import.end():]
        else:
            first_import = re.search(r"^(?P<indent>\s*)from app\.[^\n]+$", text, re.MULTILINE)
            if not first_import:
                raise RuntimeError("Could not add the Attendance launcher import to app/__init__.py")
            insert = f"\n{first_import.group('indent')}from app.attendance_launch.routes import attendance_launch_bp"
            text = text[:first_import.end()] + insert + text[first_import.end():]

    register_line = "app.register_blueprint(attendance_launch_bp)"
    if register_line not in text:
        claims_register = re.search(
            r"^(?P<indent>\s*)app\.register_blueprint\(claims_launch_bp\)\s*$",
            text,
            re.MULTILINE,
        )
        if claims_register:
            insert = f"\n{claims_register.group('indent')}{register_line}"
            text = text[:claims_register.end()] + insert + text[claims_register.end():]
        else:
            match = re.search(r"^(\s*)app\.register_blueprint\([^\n]+\)", text, re.MULTILINE)
            if not match:
                raise RuntimeError("Could not register the Attendance launcher blueprint")
            text = text[:match.end()] + f"\n{match.group(1)}{register_line}" + text[match.end():]
    app_init.write_text(text, encoding="utf-8")

    base_text = base.read_text(encoding="utf-8")
    old = "url_for('attendance.index')"
    new = "url_for('attendance_launch.launch')"
    if old in base_text:
        base.write_text(base_text.replace(old, new), encoding="utf-8")
    elif new not in base_text:
        raise RuntimeError("Could not find the Attendance sidebar links in the main template")


def install_attendance(attendance: Path) -> None:
    backend = attendance / "backend"
    main_py = backend / "app" / "main.py"
    config_py = backend / "app" / "core" / "config.py"
    app_jsx = attendance / "frontend" / "src" / "App.jsx"
    if not main_py.exists() or not config_py.exists() or not app_jsx.exists():
        raise RuntimeError("The Attendance folder does not look correct.")

    copy_file(PAYLOAD / "attendance" / "backend" / "app" / "api" / "martins_launch.py", backend / "app" / "api" / "martins_launch.py")
    copy_file(PAYLOAD / "attendance" / "frontend" / "src" / "martinsLaunch.js", attendance / "frontend" / "src" / "martinsLaunch.js")

    config_text = config_py.read_text(encoding="utf-8")
    if "ATTENDANCE_LAUNCH_SECRET" not in config_text:
        match = re.search(r"^(\s*FRONTEND_URL:.*\n)", config_text, re.MULTILINE)
        if not match:
            raise RuntimeError("Could not add launch settings to the Attendance configuration")
        addition = "    ATTENDANCE_LAUNCH_SECRET: str = \"\"\n    MARTINS_MAIN_APP_URL: str = \"\"\n"
        config_text = config_text[:match.end()] + addition + config_text[match.end():]
        config_py.write_text(config_text, encoding="utf-8")

    main_text = main_py.read_text(encoding="utf-8")
    if "martins_launch" not in main_text:
        import_match = re.search(r"from app\.api import \((.*?)\)", main_text, re.DOTALL)
        if import_match:
            items = import_match.group(1).rstrip()
            prefix = "," if not items.rstrip().endswith(",") else ""
            main_text = main_text[:import_match.start(1)] + items + prefix + "\n    martins_launch," + main_text[import_match.end(1):]
        else:
            auth_import = "from app.api import auth"
            if auth_import not in main_text:
                raise RuntimeError("Could not add the Attendance launch router import")
            main_text = main_text.replace(auth_import, auth_import + ", martins_launch", 1)

    include = "app.include_router(martins_launch.router, prefix=\"/api/auth\", tags=[\"auth\"])"
    if include not in main_text:
        auth_router = re.search(r"^(\s*)app\.include_router\(auth\.router[^\n]*\)", main_text, re.MULTILINE)
        if not auth_router:
            raise RuntimeError("Could not register the Attendance launch router")
        main_text = main_text[:auth_router.end()] + f"\n{auth_router.group(1)}{include}" + main_text[auth_router.end():]
    main_py.write_text(main_text, encoding="utf-8")

    app_text = app_jsx.read_text(encoding="utf-8")
    import_line = "import { consumeMartinsLaunch, returnToMartinsIfLaunched } from './martinsLaunch'\n"
    if "from './martinsLaunch'" not in app_text and 'from "./martinsLaunch"' not in app_text:
        first_import = re.search(r"^import [^\n]+\n", app_text, re.MULTILINE)
        if not first_import:
            raise RuntimeError("Could not add the Attendance launch client import")
        app_text = app_text[:first_import.end()] + import_line + app_text[first_import.end():]

    effect_marker = "// Martins unified-system launch handoff"
    if effect_marker not in app_text:
        component = re.search(r"(export default function App\(\)\s*\{|function App\(\)\s*\{)", app_text)
        if not component:
            raise RuntimeError("Could not add the Martins launch handler to App.jsx")
        effect = '''\n  // Martins unified-system launch handoff\n  useEffect(() => {\n    const launchToken = new URLSearchParams(window.location.search).get('martins_launch')\n    if (!launchToken) return undefined\n\n    let cancelled = false\n    consumeMartinsLaunch(launchToken)\n      .then((payload) => {\n        if (cancelled) return\n        setAccessToken(payload.access_token)\n        setToken(payload.access_token)\n        const url = new URL(window.location.href)\n        url.searchParams.delete('martins_launch')\n        window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)\n      })\n      .catch((error) => {\n        console.error(error)\n        window.alert(error.message || 'Unable to open Attendance.')\n      })\n\n    return () => { cancelled = true }\n  }, [])\n'''
        app_text = app_text[:component.end()] + effect + app_text[component.end():]

    if "returnToMartinsIfLaunched()" not in app_text:
        logout = re.search(
            r"(const handleLogout\s*=\s*\(\)\s*=>\s*\{.*?setEntities\(\[\]\))(\s*\})",
            app_text,
            re.DOTALL,
        )
        if not logout:
            raise RuntimeError("Could not add the Martins return action to Attendance logout")
        app_text = app_text[:logout.end(1)] + "\n    returnToMartinsIfLaunched()" + app_text[logout.end(1):]
    app_jsx.write_text(app_text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python install_attendance_launch.py <martins-unified-system> <attendance-register>")

    unified = Path(sys.argv[1]).expanduser().resolve()
    attendance = Path(sys.argv[2]).expanduser().resolve()
    if not unified.exists() or not attendance.exists():
        raise SystemExit("Both project folders must exist.")

    main_backup = backup([unified / "app" / "__init__.py", unified / "app" / "templates" / "base.html"], unified, "unified")
    attendance_backup = backup([
        attendance / "backend" / "app" / "main.py",
        attendance / "backend" / "app" / "core" / "config.py",
        attendance / "frontend" / "src" / "App.jsx",
    ], attendance, "attendance")

    install_unified(unified)
    install_attendance(attendance)
    print("Attendance launcher installed.")
    print(f"Main backup: {main_backup}")
    print(f"Attendance backup: {attendance_backup}")
    print("Set ATTENDANCE_APP_URL and the shared ATTENDANCE_LAUNCH_SECRET on Martins.")
    print("Set ATTENDANCE_LAUNCH_SECRET and MARTINS_MAIN_APP_URL on Attendance, then deploy both applications.")


if __name__ == "__main__":
    main()
