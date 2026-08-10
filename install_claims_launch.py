from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload" / "unified" / "app" / "claims_launch"


def require(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Required file was not found: {path}")


def write_backup(backup_root: Path, source: Path, relative_name: str) -> None:
    target = backup_root / relative_name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def replace_once(content: str, old: str, new: str, label: str) -> str:
    if new in content:
        return content
    if old not in content:
        raise RuntimeError(f"Could not find the expected {label} section.")
    return content.replace(old, new, 1)


def install_unified(unified: Path, backup_root: Path) -> None:
    init_file = unified / "app" / "__init__.py"
    base_file = unified / "app" / "templates" / "base.html"
    require(init_file)
    require(base_file)
    write_backup(backup_root, init_file, "unified/app/__init__.py")
    write_backup(backup_root, base_file, "unified/app/templates/base.html")

    destination = unified / "app" / "claims_launch"
    if not destination.exists():
        shutil.copytree(PAYLOAD, destination)

    init_text = init_file.read_text(encoding="utf-8")
    if "from app.claims_launch.routes import claims_launch_bp" not in init_text:
        init_text = replace_once(
            init_text,
            "from app.insurance_claims.routes import insurance_claims_bp",
            "from app.insurance_claims.routes import insurance_claims_bp\nfrom app.claims_launch.routes import claims_launch_bp",
            "Claims blueprint import",
        )
    if "app.register_blueprint(claims_launch_bp)" not in init_text:
        init_text = replace_once(
            init_text,
            "app.register_blueprint(insurance_claims_bp)",
            "app.register_blueprint(insurance_claims_bp)\n    app.register_blueprint(claims_launch_bp)",
            "Claims blueprint registration",
        )
    init_file.write_text(init_text, encoding="utf-8")

    base_text = base_file.read_text(encoding="utf-8")
    base_text = base_text.replace(
        "url_for('insurance_claims.index')", "url_for('claims_launch.launch')"
    )
    nav = """                        {% if current_user.has_permission('insurance_claims:view') %}
                        <details class=\"nav-section nav-domain\" open>
                            <summary><span class=\"nav-icon\">C</span><span><strong>Insurance Claims</strong><small>Premiums, claims and risk analysis</small></span></summary>
                            <div class=\"nav-subtabs\">
                                <a href=\"{{ url_for('claims_launch.launch') }}\">Open Claims Workspace <small>Full claims dashboard and operations</small></a>
                            </div>
                        </details>
                        {% endif %}
"""
    intelligence_summary = '<summary><span class="nav-icon">◎</span><span><strong>Franchise Intelligence</strong>'
    if "Open Claims Workspace" not in base_text:
        intelligence_index = base_text.find(intelligence_summary)
        if intelligence_index < 0:
            raise RuntimeError("Could not find the main sidebar insertion point.")
        marker_index = base_text.rfind("{% if ", 0, intelligence_index)
        if marker_index < 0:
            raise RuntimeError("Could not locate the end of the Royalties section.")
        base_text = base_text[:marker_index] + nav + "                        " + base_text[marker_index:]
    base_file.write_text(base_text, encoding="utf-8")


def install_claims(claims: Path, backup_root: Path) -> None:
    app_file = claims / "app.py"
    require(app_file)
    write_backup(backup_root, app_file, "claims/app.py")
    text = app_file.read_text(encoding="utf-8")

    if "from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer" not in text:
        text = replace_once(
            text,
            "from werkzeug.security import generate_password_hash, check_password_hash",
            "from werkzeug.security import generate_password_hash, check_password_hash\nfrom itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer",
            "Claims launch imports",
        )

    if "def _upsert_unified_launch_user" not in text:
        helpers = '''\n\nCLAIMS_LAUNCH_SALT = "martins-claims-launch-v1"\n\ndef _claims_launch_serializer():\n    secret = os.getenv("CLAIMS_LAUNCH_SECRET", "").strip()\n    if not secret:\n        raise RuntimeError("Claims launch is not configured.")\n    return URLSafeTimedSerializer(secret, salt=CLAIMS_LAUNCH_SALT)\n\ndef _normalise_launch_franchises(value):\n    if not isinstance(value, list):\n        return []\n    return sorted({str(item).strip() for item in value if str(item).strip()})\n\ndef _upsert_unified_launch_user(payload):\n    email = str(payload.get("email") or "").strip().lower()\n    name = str(payload.get("name") or email).strip()\n    if not email:\n        raise ValueError("Missing Martins account email.")\n    is_admin = bool(payload.get("is_admin"))\n    role = "admin" if is_admin else "franchise_user"\n    franchise_names = _normalise_launch_franchises(payload.get("franchises"))\n    engine = get_db_engine()\n    if engine is None:\n        raise RuntimeError("Claims database is unavailable.")\n    with engine.begin() as conn:\n        existing = conn.execute(text("SELECT id FROM app_users WHERE LOWER(TRIM(email)) = :email"), {"email": email}).mappings().first()\n        if existing:\n            user_id = existing["id"]\n            conn.execute(text("UPDATE app_users SET name=:name, role=:role, is_active=true, is_super_admin=:is_admin WHERE id=:id"), {"name": name, "role": role, "is_admin": is_admin, "id": user_id})\n        else:\n            user_id = conn.execute(text("INSERT INTO app_users (name, email, password_hash, role, is_active, is_super_admin) VALUES (:name, :email, :password_hash, :role, true, :is_admin) RETURNING id"), {"name": name, "email": email, "password_hash": generate_password_hash(os.urandom(24).hex()), "role": role, "is_admin": is_admin}).scalar_one()\n        conn.execute(text("DELETE FROM app_user_franchise_access WHERE user_id=:user_id"), {"user_id": user_id})\n        for franchise_name in franchise_names:\n            conn.execute(text("INSERT INTO app_user_franchise_access (user_id, franchise_name) VALUES (:user_id, :franchise_name) ON CONFLICT DO NOTHING"), {"user_id": user_id, "franchise_name": franchise_name})\n    return user_id\n'''
        text = replace_once(text, "PUBLIC_ENDPOINTS = {", helpers + "\nPUBLIC_ENDPOINTS = {", "Claims launch helper insertion")

    if "'unified_launch'" not in text:
        text = replace_once(text, "'cron_daily_backup'}", "'cron_daily_backup', 'unified_launch'}", "Claims public endpoint list")

    if "def unified_launch():" not in text:
        route = '''\n\n@app.route('/auth/launch')\ndef unified_launch():\n    token = (request.args.get('token') or '').strip()\n    if not token:\n        flash('Open Claims from Martins System to continue.', 'danger')\n        return redirect(url_for('login'))\n    try:\n        max_age = int(os.getenv('CLAIMS_LAUNCH_TOKEN_MAX_AGE', '90'))\n        payload = _claims_launch_serializer().loads(token, max_age=max_age)\n    except SignatureExpired:\n        flash('The Martins launch link has expired. Please open Claims again.', 'warning')\n        return redirect(url_for('login'))\n    except (BadSignature, RuntimeError):\n        flash('The Martins launch link could not be verified.', 'danger')\n        return redirect(url_for('login'))\n    if not isinstance(payload, dict) or payload.get('module') != 'claims':\n        flash('The Martins launch link is invalid.', 'danger')\n        return redirect(url_for('login'))\n    try:\n        user_id = _upsert_unified_launch_user(payload)\n    except Exception:\n        app.logger.exception('Unable to create Claims launch session')\n        flash('Unable to open the Claims workspace. Please contact the administrator.', 'danger')\n        return redirect(url_for('login'))\n    session.clear()\n    session['user_id'] = user_id\n    session['martins_unified_launch'] = True\n    session.permanent = True\n    record_login_success(user_id)\n    return redirect(url_for('dashboard'))\n'''
        text = replace_once(text, "@app.route('/logout')", route + "\n@app.route('/logout')", "Claims launch route insertion")

    old_logout = """@app.route('/logout')
def logout():
    if getattr(g, 'user', None):
        log_audit('logout', f\"User logged out: {g.user.get('email')}\")
    session.clear()
    flash('Logged out.', 'success')
    return redirect(url_for('login'))
"""
    new_logout = """@app.route('/logout')
def logout():
    if getattr(g, 'user', None):
        log_audit('logout', f\"User logged out: {g.user.get('email')}\")
    return_to_main = bool(session.get('martins_unified_launch'))
    session.clear()
    if return_to_main:
        main_url = os.getenv('MARTINS_MAIN_APP_URL', '').strip().rstrip('/')
        if main_url:
            return redirect(main_url + '/')
    flash('Logged out.', 'success')
    return redirect(url_for('login'))
"""
    text = replace_once(text, old_logout, new_logout, "Claims logout handoff")
    app_file.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python install_claims_launch.py <unified-system-folder> <claims-system-folder>")
    unified = Path(sys.argv[1]).expanduser().resolve()
    claims = Path(sys.argv[2]).expanduser().resolve()
    backup_root = unified / f"claims-launch-backup-{datetime.now():%Y%m%d-%H%M%S}"
    backup_root.mkdir(parents=True, exist_ok=False)
    install_unified(unified, backup_root)
    install_claims(claims, backup_root)
    print("Claims launcher installed.")
    print(f"Backup created: {backup_root}")
    print("Set the two shared Claims launch settings, then redeploy both applications.")


if __name__ == "__main__":
    main()
