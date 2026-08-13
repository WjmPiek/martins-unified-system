
from datetime import datetime, timezone, date, time
from io import BytesIO
import csv
import hashlib
import hmac
import math
import secrets
import struct
import time as time_module

from flask import Blueprint, Response, flash, redirect, render_template, request, send_file, url_for, current_app
from flask_login import current_user, login_required
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from sqlalchemy import func, or_

from app.extensions import db
from app.models import AttendanceStaff, AttendanceOffice, AttendanceEvent, AttendanceLeaveRequest, Franchise
from app.franchise_context import get_accessible_franchises, get_selected_franchise

attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")

FALLBACK_CODE_PERIOD_SECONDS = 300


def _can(action="view"):
    return current_user.is_authenticated and current_user.has_permission(f"attendance:{action}")


def _require(action="view"):
    if not _can(action):
        flash("You do not have permission to access Attendance.", "danger")
        return False
    return True


def _franchise_scope():
    selected = get_selected_franchise()
    franchises = get_accessible_franchises()
    if selected:
        return selected, franchises
    return None, franchises


def _staff_query():
    selected, franchises = _franchise_scope()
    q = AttendanceStaff.query
    if selected:
        q = q.filter(AttendanceStaff.franchise_id == selected.id)
    elif not (current_user.has_permission("franchise_management:view") or current_user.has_permission("franchise_management:manage")):
        ids = [f.id for f in franchises]
        q = q.filter(AttendanceStaff.franchise_id.in_(ids)) if ids else q.filter(False)
    return q


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _distance_m(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return None
    radius = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return round(radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)), 2)


def _fallback_code(office, at_timestamp=None):
    """Return the office's four-digit, five-minute fallback code."""
    timestamp = int(time_module.time() if at_timestamp is None else at_timestamp)
    counter = timestamp // FALLBACK_CODE_PERIOD_SECONDS
    secret = f"{current_app.secret_key}:{office.qr_token}".encode("utf-8")
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha256).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10000:04d}"


def _valid_fallback_code(office, submitted_code, at_timestamp=None):
    code = (submitted_code or "").strip()
    return len(code) == 4 and code.isdigit() and hmac.compare_digest(
        code, _fallback_code(office, at_timestamp)
    )


def _attendance_event(office, source, work_location_type):
    employee_number = request.form.get("employee_number", "").strip()
    action = request.form.get("action", "sign_in")
    if action not in {"sign_in", "sign_out"}:
        flash("Select a valid attendance action.", "danger")
        return False

    staff = AttendanceStaff.query.filter_by(
        employee_number=employee_number,
        franchise_id=office.franchise_id,
        is_active=True,
    ).first()
    if not staff:
        flash("No active staff member was found for that employee number at this office.", "danger")
        return False

    try:
        latitude = float(request.form["latitude"]) if request.form.get("latitude") else None
        longitude = float(request.form["longitude"]) if request.form.get("longitude") else None
        accuracy = float(request.form["accuracy"]) if request.form.get("accuracy") else None
    except (TypeError, ValueError):
        flash("The phone supplied an invalid location. Please refresh and try again.", "danger")
        return False

    distance = _distance_m(latitude, longitude, office.latitude, office.longitude)
    gps_status = "inside_radius" if distance is not None and distance <= office.allowed_radius_m else (
        "outside_radius" if distance is not None else "not_checked"
    )
    event = AttendanceEvent(
        staff_id=staff.id,
        franchise_id=staff.franchise_id,
        office_id=office.id,
        action=action,
        latitude=latitude,
        longitude=longitude,
        accuracy_meters=accuracy,
        distance_from_site_m=distance,
        gps_status=gps_status,
        work_location_type=work_location_type,
        source=source,
        device_info=request.headers.get("User-Agent", ""),
        employee_note=request.form.get("employee_note", "").strip(),
    )
    db.session.add(event)
    db.session.commit()
    flash(f"{staff.full_name} {action.replace('_', ' ')} recorded.", "success")
    return True


@attendance_bp.route("/")
@login_required
def index():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    selected, franchises = _franchise_scope()
    staff_q = _staff_query()
    today = datetime.now(timezone.utc).date()
    start_dt = datetime.combine(today, time.min).replace(tzinfo=timezone.utc)
    total_staff = staff_q.count()
    active_staff = staff_q.filter_by(is_active=True).count()
    staff_ids = [s.id for s in staff_q.with_entities(AttendanceStaff.id).all()]
    today_events = AttendanceEvent.query.filter(AttendanceEvent.staff_id.in_(staff_ids), AttendanceEvent.event_time >= start_dt).count() if staff_ids else 0
    pending_events = AttendanceEvent.query.filter(AttendanceEvent.staff_id.in_(staff_ids), AttendanceEvent.approval_status == "pending").count() if staff_ids else 0
    pending_leave = AttendanceLeaveRequest.query.filter(AttendanceLeaveRequest.staff_id.in_(staff_ids), AttendanceLeaveRequest.status == "pending").count() if staff_ids else 0
    recent_events = AttendanceEvent.query.filter(AttendanceEvent.staff_id.in_(staff_ids)).order_by(AttendanceEvent.event_time.desc()).limit(20).all() if staff_ids else []
    return render_template("attendance/index.html", selected=selected, franchises=franchises, total_staff=total_staff, active_staff=active_staff, today_events=today_events, pending_events=pending_events, pending_leave=pending_leave, recent_events=recent_events)


@attendance_bp.route("/service-worker.js")
def service_worker():
    """Keep old attendance browser registrations from producing repeated 404s."""
    return Response(
        "self.addEventListener('fetch', function () {});",
        mimetype="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@attendance_bp.route("/staff", methods=["GET", "POST"])
@login_required
def staff():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    selected, franchises = _franchise_scope()
    if request.method == "POST":
        if not _require("add"):
            return redirect(url_for("attendance.staff"))
        franchise_id = request.form.get("franchise_id") or (selected.id if selected else None)
        item = AttendanceStaff(
            franchise_id=franchise_id,
            first_name=request.form.get("first_name", "").strip(),
            surname=request.form.get("surname", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            id_number=request.form.get("id_number", "").strip(),
            employee_number=request.form.get("employee_number", "").strip(),
            position=request.form.get("position", "").strip(),
            staff_type=request.form.get("staff_type", "Employee").strip() or "Employee",
            website_url=request.form.get("website_url", "").strip(),
            notes=request.form.get("notes", "").strip(),
            created_by_id=current_user.id,
        )
        if not item.first_name or not item.surname:
            flash("First name and surname are required.", "danger")
        else:
            db.session.add(item); db.session.commit(); flash("Staff member added.", "success")
            return redirect(url_for("attendance.staff"))
    q = _staff_query()
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(AttendanceStaff.first_name.ilike(like), AttendanceStaff.surname.ilike(like), AttendanceStaff.email.ilike(like), AttendanceStaff.employee_number.ilike(like)))
    staff_rows = q.order_by(AttendanceStaff.surname, AttendanceStaff.first_name).all()
    return render_template("attendance/staff.html", staff_rows=staff_rows, franchises=franchises, selected=selected, search=search)


@attendance_bp.route("/staff/<int:staff_id>/toggle", methods=["POST"])
@login_required
def staff_toggle(staff_id):
    if not _require("edit"):
        return redirect(url_for("attendance.staff"))
    staff = _staff_query().filter_by(id=staff_id).first_or_404()
    staff.is_active = not staff.is_active
    db.session.commit()
    flash("Staff status updated.", "success")
    return redirect(url_for("attendance.staff"))


@attendance_bp.route("/offices", methods=["GET", "POST"])
@login_required
def offices():
    if not _require("view"):
        return redirect(url_for("dashboard.index"))
    selected, franchises = _franchise_scope()
    if request.method == "POST":
        if not _require("manage"):
            return redirect(url_for("attendance.offices"))
        franchise_id = request.form.get("franchise_id") or (selected.id if selected else None)
        office = AttendanceOffice(
            franchise_id=franchise_id,
            name=request.form.get("name", "Office").strip() or "Office",
            address=request.form.get("address", "").strip(),
            latitude=float(request.form["latitude"]) if request.form.get("latitude") else None,
            longitude=float(request.form["longitude"]) if request.form.get("longitude") else None,
            allowed_radius_m=int(request.form.get("allowed_radius_m") or 100),
            qr_token=secrets.token_urlsafe(32),
            created_by_id=current_user.id,
        )
        db.session.add(office); db.session.commit(); flash("Office QR location added.", "success")
        return redirect(url_for("attendance.offices"))
    q = AttendanceOffice.query
    if selected:
        q = q.filter_by(franchise_id=selected.id)
    offices = q.order_by(AttendanceOffice.name).all()
    return render_template("attendance/offices.html", offices=offices, franchises=franchises, selected=selected)


@attendance_bp.route("/offices/<int:office_id>/qr.png")
@login_required
def office_qr_image(office_id):
    if not _require("view"):
        return redirect(url_for("attendance.index"))
    office = AttendanceOffice.query.filter_by(id=office_id, is_active=True).first_or_404()
    scan_url = url_for("attendance.scan", token=office.qr_token, _external=True)
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(scan_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return send_file(
        output,
        mimetype="image/png",
        download_name=f"office-qr-{office.id}.png",
        max_age=0,
    )


@attendance_bp.route("/offices/<int:office_id>/print")
@login_required
def office_qr_print(office_id):
    if not _require("view"):
        return redirect(url_for("attendance.index"))
    office = AttendanceOffice.query.filter_by(id=office_id, is_active=True).first_or_404()
    return render_template("attendance/office_qr_print.html", office=office)


@attendance_bp.route("/offices/<int:office_id>/fallback-code")
@login_required
def office_fallback_code(office_id):
    if not _require("view"):
        return {"error": "forbidden"}, 403
    office = AttendanceOffice.query.filter_by(id=office_id, is_active=True).first_or_404()
    now = int(time_module.time())
    return {
        "code": _fallback_code(office, now),
        "expires_in": FALLBACK_CODE_PERIOD_SECONDS - (now % FALLBACK_CODE_PERIOD_SECONDS),
    }, 200, {"Cache-Control": "no-store"}


@attendance_bp.route("/scan/<token>", methods=["GET", "POST"])
def scan(token):
    office = AttendanceOffice.query.filter_by(qr_token=token, is_active=True).first_or_404()
    if request.method == "POST":
        if _attendance_event(office, "office_qr", "Office"):
            return redirect(url_for("attendance.scan", token=token))
    return render_template("attendance/scan.html", office=office, mode="office_qr")


@attendance_bp.route("/code/<int:office_id>", methods=["GET", "POST"])
def fallback_scan(office_id):
    office = AttendanceOffice.query.filter_by(id=office_id, is_active=True).first_or_404()
    if request.method == "POST":
        if not _valid_fallback_code(office, request.form.get("fallback_code")):
            flash("That four-digit code is invalid or has expired. Ask the office for the current code.", "danger")
        elif _attendance_event(office, "time_code", "Remote"):
            return redirect(url_for("attendance.fallback_scan", office_id=office.id))
    return render_template("attendance/scan.html", office=office, mode="time_code")


@attendance_bp.route("/manual", methods=["GET", "POST"])
@login_required
def manual_event():
    if not _require("add"):
        return redirect(url_for("attendance.index"))
    staff_rows = _staff_query().filter_by(is_active=True).order_by(AttendanceStaff.surname).all()
    if request.method == "POST":
        staff_id = int(request.form.get("staff_id"))
        staff = _staff_query().filter_by(id=staff_id).first_or_404()
        ev = AttendanceEvent(staff_id=staff.id, franchise_id=staff.franchise_id, action=request.form.get("action", "sign_in"), event_time=datetime.strptime(request.form.get("event_time"), "%Y-%m-%dT%H:%M"), work_location_type=request.form.get("work_location_type", "Manual"), source="manual", employee_note=request.form.get("note", ""), approval_status="approved", approved_by_id=current_user.id, approved_at=datetime.now(timezone.utc))
        db.session.add(ev); db.session.commit(); flash("Manual attendance event saved.", "success")
        return redirect(url_for("attendance.history"))
    return render_template("attendance/manual.html", staff_rows=staff_rows)


@attendance_bp.route("/history")
@login_required
def history():
    if not _require("view"):
        return redirect(url_for("attendance.index"))
    q = AttendanceEvent.query.join(AttendanceStaff)
    staff_ids = [s.id for s in _staff_query().with_entities(AttendanceStaff.id).all()]
    q = q.filter(AttendanceEvent.staff_id.in_(staff_ids)) if staff_ids else q.filter(False)
    from_date = _parse_date(request.args.get("from_date"))
    to_date = _parse_date(request.args.get("to_date"))
    if from_date:
        q = q.filter(AttendanceEvent.event_time >= datetime.combine(from_date, time.min))
    if to_date:
        q = q.filter(AttendanceEvent.event_time <= datetime.combine(to_date, time.max))
    events = q.order_by(AttendanceEvent.event_time.desc()).limit(500).all()
    return render_template("attendance/history.html", events=events)


@attendance_bp.route("/approvals", methods=["GET", "POST"])
@login_required
def approvals():
    if not _require("approve"):
        return redirect(url_for("attendance.index"))
    if request.method == "POST":
        staff_ids = [s.id for s in _staff_query().with_entities(AttendanceStaff.id).all()]
        event = AttendanceEvent.query.filter(
            AttendanceEvent.id == int(request.form.get("event_id")),
            AttendanceEvent.staff_id.in_(staff_ids),
        ).first_or_404()
        action = request.form.get("decision")
        if action == "approve":
            event.approval_status = "approved"; event.approved_by_id = current_user.id; event.approved_at = datetime.now(timezone.utc)
        elif action == "reject":
            event.approval_status = "rejected"; event.rejected_reason = request.form.get("reason", ""); event.approved_by_id = current_user.id; event.approved_at = datetime.now(timezone.utc)
        event.manager_note = request.form.get("manager_note", "")
        db.session.commit(); flash("Attendance approval updated.", "success")
        return redirect(url_for("attendance.approvals"))
    staff_ids = [s.id for s in _staff_query().with_entities(AttendanceStaff.id).all()]
    events = AttendanceEvent.query.filter(AttendanceEvent.staff_id.in_(staff_ids), AttendanceEvent.approval_status == "pending").order_by(AttendanceEvent.event_time.desc()).all() if staff_ids else []
    return render_template("attendance/approvals.html", events=events)


@attendance_bp.route("/leave", methods=["GET", "POST"])
@login_required
def leave():
    if not _require("view"):
        return redirect(url_for("attendance.index"))
    staff_rows = _staff_query().filter_by(is_active=True).order_by(AttendanceStaff.surname).all()
    if request.method == "POST":
        if not _require("add"):
            return redirect(url_for("attendance.leave"))
        staff = _staff_query().filter_by(id=int(request.form.get("staff_id"))).first_or_404()
        lr = AttendanceLeaveRequest(staff_id=staff.id, franchise_id=staff.franchise_id, leave_type=request.form.get("leave_type", "Annual Leave"), start_date=_parse_date(request.form.get("start_date")), end_date=_parse_date(request.form.get("end_date")), reason=request.form.get("reason", ""))
        db.session.add(lr); db.session.commit(); flash("Leave request added.", "success")
        return redirect(url_for("attendance.leave"))
    staff_ids = [s.id for s in staff_rows]
    requests = AttendanceLeaveRequest.query.filter(AttendanceLeaveRequest.staff_id.in_(staff_ids)).order_by(AttendanceLeaveRequest.created_at.desc()).all() if staff_ids else []
    return render_template("attendance/leave.html", staff_rows=staff_rows, requests=requests)


@attendance_bp.route("/leave/<int:request_id>/<decision>", methods=["POST"])
@login_required
def leave_decision(request_id, decision):
    if not _require("approve"):
        return redirect(url_for("attendance.leave"))
    staff_ids = [s.id for s in _staff_query().with_entities(AttendanceStaff.id).all()]
    lr = AttendanceLeaveRequest.query.filter(
        AttendanceLeaveRequest.id == request_id,
        AttendanceLeaveRequest.staff_id.in_(staff_ids),
    ).first_or_404()
    if decision in ("approved", "declined"):
        lr.status = decision; lr.decided_by_id = current_user.id; lr.decided_at = datetime.now(timezone.utc); lr.manager_note = request.form.get("manager_note", "")
        db.session.commit(); flash("Leave request updated.", "success")
    return redirect(url_for("attendance.leave"))


@attendance_bp.route("/export.csv")
@login_required
def export_csv():
    if not _require("export"):
        return redirect(url_for("attendance.history"))
    staff_ids = [s.id for s in _staff_query().with_entities(AttendanceStaff.id).all()]
    events = AttendanceEvent.query.filter(AttendanceEvent.staff_id.in_(staff_ids)).order_by(AttendanceEvent.event_time.desc()).all() if staff_ids else []
    out = BytesIO(); text = []
    text.append(["Staff", "Employee Number", "Franchise", "Action", "Time", "Office", "GPS Status", "Distance", "Approval"])
    for e in events:
        text.append([e.staff.full_name, e.staff.employee_number, e.franchise.business_name if e.franchise else "", e.action, e.event_time.isoformat(), e.office.name if e.office else "", e.gps_status, e.distance_from_site_m, e.approval_status])
    s = "\n".join(",".join('"'+str(c).replace('"','""')+'"' for c in row) for row in text)
    return Response(s, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=attendance_history.csv"})


@attendance_bp.route("/id-card/<int:staff_id>")
@login_required
def id_card(staff_id):
    if not _require("view"):
        return redirect(url_for("attendance.staff"))
    staff = _staff_query().filter_by(id=staff_id).first_or_404()
    return render_template("attendance/id_card.html", staff=staff)
