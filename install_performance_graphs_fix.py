"""Repair graph-cache recovery and restrict target editing to Admin users.

Run this file from the Martins unified system folder, next to run.py.
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise RuntimeError(message)


def replace_all(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        fail(f"Could not find {label}. This installer needs the current unified-system files.")
    return text.replace(old, new)


def add_decorator_after(text: str, permission: str, decorator: str) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    for index, line in enumerate(lines):
        output.append(line)
        if line.strip() == permission and (index + 1 == len(lines) or lines[index + 1].strip() != f"@{decorator}"):
            output.append(f"@{decorator}\n")
    return "".join(output)


def add_performance_target_guard(text: str) -> str:
    if "def can_manage_performance_targets()" in text:
        return text
    marker = "\ndef request_mode():\n"
    guard = '''

PERFORMANCE_TARGET_ADMIN_ROLES = {"Admin", "Super Admin"}


def can_manage_performance_targets():
    return current_user.is_authenticated and any(
        role.name in PERFORMANCE_TARGET_ADMIN_ROLES for role in current_user.roles
    )


def performance_target_admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not can_manage_performance_targets():
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped
'''
    if marker not in text:
        fail("Could not add the Performance target permission guard.")
    return text.replace(marker, guard + marker, 1)


def add_leaderboard_target_guard(text: str) -> str:
    if "def can_manage_leaderboard_targets()" in text:
        return text
    marker = "\ndef selected_period():\n"
    guard = '''

LEADERBOARD_TARGET_ADMIN_ROLES = {"Admin", "Super Admin"}


def can_manage_leaderboard_targets():
    return current_user.is_authenticated and any(
        role.name in LEADERBOARD_TARGET_ADMIN_ROLES for role in current_user.roles
    )


def leaderboard_target_admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not can_manage_leaderboard_targets():
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped
'''
    if marker not in text:
        fail("Could not add the Leaderboard target permission guard.")
    return text.replace(marker, guard + marker, 1)


def backup(files: list[Path]) -> None:
    folder = ROOT / f"performance-graphs-targets-backup-{datetime.now():%Y%m%d-%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    for file_path in files:
        destination = folder / file_path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, destination)
    print(f"Backup created: {folder}")


def main() -> None:
    performance_routes = ROOT / "app" / "performance" / "routes.py"
    performance_service = ROOT / "app" / "performance" / "service.py"
    leaderboard_routes = ROOT / "app" / "leaderboard" / "routes.py"
    files = [performance_routes, performance_service, leaderboard_routes]
    missing = [str(file_path) for file_path in files if not file_path.exists()]
    if missing:
        fail("Missing required files:\n" + "\n".join(missing))

    backup(files)

    routes_text = performance_routes.read_text(encoding="utf-8")
    routes_text = add_performance_target_guard(routes_text)
    routes_text = replace_all(
        routes_text,
        'not current_user.has_permission("performance:manage_targets")',
        "not can_manage_performance_targets()",
        "Performance target access check",
    )
    routes_text = routes_text.replace(
        'current_user.has_permission("performance:manage_targets")',
        "can_manage_performance_targets()",
    )
    routes_text = add_decorator_after(
        routes_text,
        '@permission_required("performance:manage_targets")',
        "performance_target_admin_required",
    )
    routes_text = routes_text.replace(
        "graph_engine_payload_for_franchises(ids, metric_key, month, year, periods, mode, growth)",
        "graph_engine_payload_for_franchises(ids, metric_key, month, year, periods, mode, growth, allow_rebuild=True)",
    )
    routes_text = routes_text.replace(
        "graph_engine_payload(franchise_id, metric_key, month, year, periods, mode, growth)",
        "graph_engine_payload(franchise_id, metric_key, month, year, periods, mode, growth, allow_rebuild=True)",
    )
    marker = "\n    return jsonify({\n        \"ok\": True,"
    if "db.session.commit()" not in routes_text[routes_text.find("def graphs_data"):routes_text.find("def graphs_data") + 4000]:
        if marker not in routes_text:
            fail("Could not add graph cache saving to the graph endpoint.")
        routes_text = routes_text.replace(marker, "\n    db.session.commit()" + marker, 1)
    performance_routes.write_text(routes_text, encoding="utf-8")

    service_text = performance_service.read_text(encoding="utf-8")
    if "def _normalise_growth_cache_value(" not in service_text:
        marker = "\ndef graph_engine_payload_for_franchises("
        helper = '''

def _normalise_growth_cache_value(growth_percent):
    """Keep equivalent values such as 1.6 and 1.60 on the same cache key."""
    try:
        return f"{Decimal(str(growth_percent)).quantize(Decimal('0.0001')):.4f}"
    except (ArithmeticError, TypeError, ValueError):
        return "0.0000"
'''
        if marker not in service_text:
            fail("Could not add the graph cache-key normaliser.")
        service_text = service_text.replace(marker, helper + marker, 1)
    service_text = service_text.replace(
        '"growth": str(growth_percent)',
        '"growth": _normalise_growth_cache_value(growth_percent)',
    ).replace(
        "'growth': str(growth_percent)",
        "'growth': _normalise_growth_cache_value(growth_percent)",
    )
    performance_service.write_text(service_text, encoding="utf-8")

    leaderboard_text = leaderboard_routes.read_text(encoding="utf-8")
    leaderboard_text = add_leaderboard_target_guard(leaderboard_text)
    leaderboard_text = leaderboard_text.replace(
        'current_user.has_permission("leaderboard:manage_targets")',
        "can_manage_leaderboard_targets()",
    )
    leaderboard_text = add_decorator_after(
        leaderboard_text,
        '@permission_required("leaderboard:manage_targets")',
        "leaderboard_target_admin_required",
    )
    leaderboard_routes.write_text(leaderboard_text, encoding="utf-8")

    for file_path in files:
        py_compile.compile(str(file_path), doraise=True)

    print("Performance graphs repaired.")
    print("Graph caches now rebuild automatically when figures exist.")
    print("Only Admin and Super Admin can edit targets.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
