import os
import re
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from markupsafe import Markup, escape
from pypdf import PdfReader
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from app.extensions import db
from app.models import MFFManual, MFFManualVersion, MFFIndexDocument, MFFManualAcknowledgement, AuditLog, User
from app.manuals import access
from app.manuals.storage import save_pdf, resolve_storage_path, seed_root

manuals_bp = Blueprint("manuals", __name__, url_prefix="/manuals")


def _can(action="view"):
    return current_user.is_authenticated and current_user.has_permission(f"manuals:{action}")


def _require(action="view"):
    if not _can(action):
        flash("You do not have permission to access Manuals.", "danger")
        return False
    return True


def _is_admin():
    return current_user.has_permission("manuals:manage") or current_user.has_permission("system_administration:view")


def _can_remove():
    return access.can_manage(current_user)


def _visible_manuals():
    query = MFFManual.query.filter(MFFManual.removed_at.is_(None))
    if access.is_owner(current_user):
        return query
    ids = [manual.id for manual in query.all() if access.can_read(current_user, manual)]
    return query.filter(MFFManual.id.in_(ids))


def _visible_indexes():
    standalone = MFFIndexDocument.manual_id.is_(None)
    if not access.is_owner(current_user):
        standalone = db.and_(standalone, ~db.func.lower(MFFIndexDocument.title).like("%territor%"))
    return MFFIndexDocument.query.filter(MFFIndexDocument.is_active.is_(True),
            or_(standalone, MFFIndexDocument.manual_id.in_(_visible_manuals().with_entities(MFFManual.id))))


def _manual_or_404(manual_id):
    return _visible_manuals().filter_by(id=manual_id).first() or abort(404)


def _version_or_404(version_id):
    version = db.session.get(MFFManualVersion, version_id) or abort(404)
    if not version.manual or not access.can_read(current_user, version.manual):
        abort(404)
    return version


def _removal_allowed(manual):
    core = access.core_module(manual.title)
    return not core or any(other.id != manual.id and access.core_module(other.title) == core
                           and _latest(other.id) for other in MFFManual.query.filter_by(removed_at=None).all())


def _latest(manual_id):
    return (MFFManualVersion.query.join(MFFManual)
            .filter(MFFManualVersion.manual_id == manual_id, MFFManualVersion.is_published.is_(True), MFFManual.removed_at.is_(None))
            .order_by(MFFManualVersion.uploaded_at.desc(), MFFManualVersion.id.desc()).first())


def _normalize_doc_name(value):
    value = (value or "").lower()
    value = re.sub(r"\.pdf$", "", value)
    value = value.replace("&", " and ")
    value = re.sub(r"\b(index|manual|module|latest|version|final|copy)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _infer_manual(name):
    normalized_name = _normalize_doc_name(name)
    scored = []
    for manual in _visible_manuals().order_by(MFFManual.title.asc()).all():
        title = _normalize_doc_name(manual.title)
        if not title:
            continue
        score = 0
        if normalized_name == title:
            score = 100
        elif title in normalized_name:
            score = 80
        elif normalized_name in title:
            score = 70
        else:
            overlap = len(set(normalized_name.split()) & set(title.split()))
            score = overlap * 10
        if score:
            scored.append((score, manual))
    scored.sort(key=lambda item: (item[0], len(item[1].title)), reverse=True)
    return scored[0][1] if scored else None


def _seed_if_empty():
    if MFFManual.query.first() or MFFIndexDocument.query.first():
        return
    root = seed_root(current_app)
    manuals_root = root / "MANUALS"
    indexes_root = root / "INDEX"
    if manuals_root.exists():
        for pdf in sorted(manuals_root.glob("*.pdf")):
            title = pdf.stem.replace("_", " ").replace("  ", " ").strip()
            manual = MFFManual(title=title, confidentiality_note="Confidential - internal use only", **access.defaults(title))
            db.session.add(manual); db.session.flush()
            v = MFFManualVersion(manual_id=manual.id, version_label="v1.0", filename=pdf.name, storage_path=str(pdf), sha256="seed", uploaded_by_user_id=None, is_published=True)
            db.session.add(v)
    if indexes_root.exists():
        db.session.flush()
        for pdf in sorted(indexes_root.glob("*.pdf")):
            linked = _infer_manual(pdf.stem)
            doc = MFFIndexDocument(title=pdf.stem.replace("_", " "), filename=pdf.name, storage_path=str(pdf), manual_id=linked.id if linked else None, uploaded_by_user_id=None, is_active=True)
            db.session.add(doc)
    db.session.commit()


def _safe_pdf_response(path, download_name, as_attachment=False):
    if not path or not Path(path).exists():
        abort(404)
    resp = send_file(path, mimetype="application/pdf", as_attachment=as_attachment, download_name=download_name)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@lru_cache(maxsize=256)
def _extract_pages(path, mtime):
    pages = []
    try:
        reader = PdfReader(path)
        for idx, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append((idx, re.sub(r"\s+", " ", text).strip()))
    except Exception:
        return []
    return pages


def _snippet(text, query, radius=110):
    if not text:
        return ""
    low = text.lower(); q = query.lower(); pos = low.find(q)
    if pos < 0:
        sample = text[:radius*2]
        return Markup(escape(sample + ("…" if len(text) > len(sample) else "")))
    start=max(0,pos-radius); end=min(len(text),pos+len(query)+radius)
    before = "…" if start else ""; after = "…" if end < len(text) else ""
    part = text[start:end]
    highlighted = re.sub(re.escape(query), lambda m: f"<mark>{escape(m.group(0))}</mark>", escape(part), flags=re.I)
    return Markup(before + highlighted + after)


def _search_documents(query, scope="all"):
    results=[]
    q=(query or "").strip()
    if not q: return results
    items=[]
    if scope in ("all","manuals"):
        versions = (MFFManualVersion.query.filter(MFFManualVersion.is_published.is_(True),
                    MFFManualVersion.manual_id.in_(_visible_manuals().with_entities(MFFManual.id))).all())
        items += [("manual", v.manual.title if v.manual else v.filename, v.version_label, v.filename, v.storage_path, url_for("manuals.download_version", version_id=v.id)) for v in versions]
    if scope in ("all","indexes"):
        docs = _visible_indexes().all()
        items += [("index", d.title, "Index document", d.filename, d.storage_path, url_for("manuals.open_index", document_id=d.id)) for d in docs]
    for kind,title,subtitle,filename,path,url in items:
        p=resolve_storage_path(current_app, path)
        if not p or not p.exists(): continue
        page_hits=[]; total=0
        for page_no,text in _extract_pages(str(p), p.stat().st_mtime):
            count=text.lower().count(q.lower())
            if count:
                total += count
                page_hits.append({"page_number":page_no,"match_count":count,"snippet":_snippet(text,q)})
        if total or q.lower() in title.lower() or q.lower() in filename.lower():
            results.append({"kind":kind,"title":title,"subtitle":subtitle,"filename":filename,"url":url,"total_matches":total,"page_results":page_hits[:8],"extra_pages":max(0,len(page_hits)-8),"snippet":page_hits[0]["snippet"] if page_hits else "Filename/title match"})
    results.sort(key=lambda r: (r["total_matches"], r["title"]), reverse=True)
    return results


@manuals_bp.before_request
def _before():
    if current_user.is_authenticated:
        try:
            _seed_if_empty()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Manual seed failed")


@manuals_bp.route("/")
@login_required
def index():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    manuals = _visible_manuals().order_by(MFFManual.title.asc()).all()
    latest_map = {m.id: _latest(m.id) for m in manuals}
    indexes_count = _visible_indexes().count()
    acknowledgements = (MFFManualAcknowledgement.query.join(MFFManualVersion)
                        .filter(MFFManualVersion.manual_id.in_(_visible_manuals().with_entities(MFFManual.id))).count())
    return render_template("manuals/index.html", manuals=manuals, latest_map=latest_map, indexes_count=indexes_count, acknowledgements=acknowledgements, is_manual_admin=_is_admin(), can_manage_manuals=_can_remove(), manual_access_labels={m.id: access.description(m) for m in manuals})


@manuals_bp.route("/library")
@login_required
def library():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    manuals = _visible_manuals().order_by(MFFManual.title.asc()).all()
    latest_map = {m.id: _latest(m.id) for m in manuals}
    return render_template("manuals/library.html", manuals=manuals, latest_map=latest_map, is_manual_admin=_is_admin(), can_manage_manuals=_can_remove(), manual_access_labels={m.id: access.description(m) for m in manuals})


@manuals_bp.route("/indexes")
@login_required
def indexes():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    docs = _visible_indexes().order_by(MFFIndexDocument.title.asc()).all()
    return render_template("manuals/indexes.html", documents=docs, is_manual_admin=_is_admin())


@manuals_bp.route("/search")
@login_required
def search():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    query=(request.args.get("q") or "").strip()
    scope=(request.args.get("scope") or "all").strip().lower()
    if scope not in {"all","manuals","indexes"}: scope="all"
    results=_search_documents(query,scope) if query else []
    total_matches=sum(r.get("total_matches") or 0 for r in results)
    return render_template("manuals/search.html", query=query, scope=scope, results=results, total_matches=total_matches, total_files=len(results))


@manuals_bp.post("/search-pdfs")
@login_required
def search_pdfs_api():
    if not _require("view"):
        return jsonify({"ok":False,"message":"Forbidden"}),403
    data=request.get_json(silent=True) or {}
    query=(data.get("query") or request.form.get("query") or "").strip()
    scope=(data.get("scope") or request.form.get("scope") or "all").strip().lower()
    results=_search_documents(query, scope) if query else []
    return jsonify({"pdf_count":len(results),"total_matches":sum(r.get("total_matches") or 0 for r in results),"results":[{"kind":r["kind"],"title":r["title"],"filename":r["filename"],"total_matches":r["total_matches"],"url":r["url"],"snippet":str(r["snippet"])} for r in results]})


@manuals_bp.route("/upload", methods=["GET","POST"])
@login_required
def upload_manual():
    if not _require("manage"):
        return redirect(url_for("manuals.index"))
    if request.method == "POST":
        title=(request.form.get("title") or "").strip(); version=(request.form.get("version_label") or "v1.0").strip(); file=request.files.get("file")
        if not title or not file or not (file.filename or "").lower().endswith(".pdf"):
            flash("Manual title and a PDF file are required.", "danger"); return redirect(url_for("manuals.upload_manual"))
        manual=MFFManual.query.filter_by(title=title, removed_at=None).first()
        if manual:
            if not access.can_read(current_user, manual):
                abort(403)
        else:
            policy = access.defaults(title)
            # New optional documents stay private until the owner chooses readers.
            if not access.core_module(title) and not policy["required_module"]:
                policy["audience"] = "selected"
            if access.territory(title) and not access.is_owner(current_user):
                abort(403)
            manual=MFFManual(title=title, confidentiality_note="Confidential - internal use only", **policy)
            if not access.is_owner(current_user) and policy["audience"] == "selected":
                manual.readers.append(access.identity(current_user))
        db.session.add(manual); db.session.flush()
        if MFFManualVersion.query.filter_by(manual_id=manual.id, version_label=version).first():
            flash("That version label already exists for this manual.", "danger"); return redirect(url_for("manuals.upload_manual"))
        MFFManualVersion.query.filter_by(manual_id=manual.id).update({"is_published":False})
        filename, storage_path, sha = save_pdf(current_app, file, "manuals", "manual")
        v=MFFManualVersion(manual_id=manual.id, version_label=version, filename=filename, storage_path=storage_path, sha256=sha, uploaded_by_user_id=current_user.id, is_published=True)
        db.session.add(v); db.session.commit(); flash("Manual uploaded and published.", "success")
        return redirect(url_for("manuals.manual_detail", manual_id=manual.id))
    return render_template("manuals/upload_manual.html")


@manuals_bp.route("/indexes/upload", methods=["GET","POST"])
@login_required
def upload_index():
    if not _require("manage"):
        return redirect(url_for("manuals.indexes"))
    manuals=_visible_manuals().order_by(MFFManual.title.asc()).all()
    if request.method == "POST":
        file=request.files.get("file"); title=(request.form.get("title") or "").strip(); manual_id=request.form.get("manual_id") or None
        if not file or not (file.filename or "").lower().endswith(".pdf"):
            flash("A PDF index file is required.", "danger"); return redirect(url_for("manuals.upload_index"))
        filename, storage_path, sha = save_pdf(current_app, file, "indexes", "index")
        linked = _manual_or_404(int(manual_id)) if manual_id else _infer_manual(title or filename)
        doc=MFFIndexDocument(title=title or Path(filename).stem, filename=filename, storage_path=storage_path, manual_id=linked.id if linked else None, uploaded_by_user_id=current_user.id, is_active=True)
        db.session.add(doc); db.session.commit(); flash("Index uploaded.", "success")
        return redirect(url_for("manuals.indexes"))
    return render_template("manuals/upload_index.html", manuals=manuals)


@manuals_bp.route("/<int:manual_id>")
@login_required
def manual_detail(manual_id):
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    manual=_manual_or_404(manual_id)
    latest=_latest(manual.id)
    versions=MFFManualVersion.query.filter_by(manual_id=manual.id).order_by(MFFManualVersion.uploaded_at.desc()).all()
    linked_indexes=MFFIndexDocument.query.filter_by(manual_id=manual.id, is_active=True).all()
    return render_template("manuals/detail.html", manual=manual, latest=latest, versions=versions, linked_indexes=linked_indexes, is_manual_admin=_is_admin(), can_manage_manuals=_can_remove(), can_remove_manual=_can_remove() and _removal_allowed(manual))


@manuals_bp.route("/<int:manual_id>/remove", methods=["GET", "POST"])
@login_required
def remove_manual(manual_id):
    if not _can_remove():
        abort(403)
    from app.registration import csrf_token, check_csrf
    manual = _manual_or_404(manual_id)
    if not _removal_allowed(manual):
        abort(400, "Modules 1-7 are compulsory. Upload a replacement version instead of removing the only copy.")
    if request.method == "POST":
        check_csrf()
        if request.form.get("confirm_remove") != str(manual.id):
            abort(400, "Confirm the selected manual before removing it.")
        manual.removed_at = datetime.now(timezone.utc)
        db.session.add(AuditLog(user_id=access.identity(current_user).id, module="manuals", action="manual_removed",
                               details=json.dumps({"manual_id": manual.id, "title": manual.title,
                                                   "removed_at": manual.removed_at.isoformat(),
                                                   "versions_retained": len(manual.versions)})))
        db.session.commit()
        flash("Manual removed from the library and search. Document and acknowledgement history has been retained.", "success")
        return redirect(url_for("manuals.library"))
    versions = MFFManualVersion.query.filter_by(manual_id=manual.id).order_by(MFFManualVersion.uploaded_at.desc()).all()
    return render_template("manuals/remove.html", manual=manual, versions=versions, csrf_token=csrf_token()), 200, {"Cache-Control": "no-store"}


@manuals_bp.route("/<int:manual_id>/access", methods=["GET", "POST"])
@login_required
def manual_access(manual_id):
    if not access.can_manage(current_user):
        abort(403)
    from app.registration import csrf_token, check_csrf
    manual = _manual_or_404(manual_id)
    core = access.core_module(manual.title)
    is_territory = access.territory(manual.title)
    inferred_module = access.defaults(manual.title)["required_module"]
    users = [u for u in User.query.filter_by(is_active=True, is_active_account=True).order_by(User.surname, User.name).all()
             if not access.is_owner(u) and (not is_territory or access.head_office(u))]
    if request.method == "POST":
        check_csrf()
        audience = request.form.get("audience", "")
        module = request.form.get("required_module", "").strip() or None
        try:
            selected = {int(value) for value in request.form.getlist("reader_ids")}
        except (TypeError, ValueError):
            abort(400, "Invalid reader selection.")
        if audience not in access.AUDIENCES or (module and module not in access.MODULES):
            abort(400, "Choose a valid audience and module.")
        if inferred_module and module != inferred_module:
            abort(400, "This module manual must follow the matching module activation.")
        if module:
            if selected:
                abort(400, "Module manuals follow activation automatically; do not select individual readers.")
            audience = "module"
        elif audience == "module":
            abort(400, "Choose a module for automatic module access.")
        if core and (audience != "all" or module or selected):
            abort(400, "Modules 1-7 must remain available to all franchise users.")
        if is_territory and (audience != "selected" or module):
            abort(400, "The Territory Manual is restricted to the owner and selected Head Office employees.")
        allowed_ids = {user.id for user in users}
        # Previously selected inactive readers can be cleared but cannot be re-granted.
        if selected - allowed_ids or (audience != "selected" and selected):
            abort(400, "Choose active eligible readers for selected-person access.")
        manual.audience = audience
        manual.required_module = module
        manual.readers = [user for user in users if user.id in selected]
        db.session.add(AuditLog(user_id=access.identity(current_user).id, module="manuals", action="manual_access_changed",
                               details=json.dumps({"manual_id": manual.id, "title": manual.title,
                                                   "audience": audience, "required_module": module,
                                                   "reader_ids": sorted(selected)})))
        db.session.commit()
        flash("Manual reader access saved.", "success")
        return redirect(url_for("manuals.manual_detail", manual_id=manual.id))
    return render_template("manuals/access.html", manual=manual, users=users, core=core,
                           is_territory=is_territory, audiences=access.AUDIENCES, modules=access.MODULES,
                           inferred_module=inferred_module,
                           selected_ids={reader.id for reader in manual.readers}, csrf_token=csrf_token()), 200, {"Cache-Control": "no-store"}


@manuals_bp.route("/<int:manual_id>/open")
@login_required
def manual_open(manual_id):
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    _manual_or_404(manual_id)
    latest=_latest(manual_id)
    if not latest:
        flash("No published version available.", "warning"); return redirect(url_for("manuals.library"))
    return redirect(url_for("manuals.view_version", version_id=latest.id))


@manuals_bp.route("/version/<int:version_id>/view")
@login_required
def view_version(version_id):
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    v=_version_or_404(version_id)
    return render_template("manuals/view.html", version=v, back_url=request.args.get("next") or url_for("manuals.manual_detail", manual_id=v.manual_id))


@manuals_bp.route("/version/<int:version_id>/download")
@login_required
def download_version(version_id):
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    v=_version_or_404(version_id)
    path=resolve_storage_path(current_app, v.storage_path)
    return _safe_pdf_response(path, v.filename, as_attachment=(request.args.get("download") == "1"))


@manuals_bp.route("/indexes/<int:document_id>/open")
@login_required
def open_index(document_id):
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    doc=_visible_indexes().filter(MFFIndexDocument.id == document_id).first() or abort(404)
    path=resolve_storage_path(current_app, doc.storage_path)
    return _safe_pdf_response(path, doc.filename, False)


@manuals_bp.post("/version/<int:version_id>/acknowledge")
@login_required
def acknowledge(version_id):
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    v=_version_or_404(version_id)
    existing=MFFManualAcknowledgement.query.filter_by(manual_version_id=v.id, user_id=current_user.id).first()
    if not existing:
        ack=MFFManualAcknowledgement(manual_version_id=v.id, user_id=current_user.id, attested_name=current_user.full_name, ip_address=request.headers.get("X-Forwarded-For", request.remote_addr) or "", user_agent=(request.headers.get("User-Agent") or "")[:500])
        db.session.add(ack); db.session.commit(); flash("Manual acknowledged.", "success")
    return redirect(url_for("manuals.manual_detail", manual_id=v.manual_id))
