from decimal import Decimal
from types import SimpleNamespace
from functools import wraps
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, abort, send_file, request
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.audit import log_action
from app.models import MonthlyFigure, Franchise, User
from app.monthly.routes import recalculate_figures_for_display, calculate_royalty_base, calculate_royalty
from app.franchise_context import enforce_franchise_access, get_selected_franchise, get_accessible_franchises, is_privileged_user, is_franchise_view_mode
from app.grouped_royalties import grouped_franchise_sets, ordered_linked_franchises_for_user

royalties_bp = Blueprint("royalties", __name__, url_prefix="/royalties")

MONTHS = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"),
    (5, "May"), (6, "June"), (7, "July"), (8, "August"),
    (9, "September"), (10, "October"), (11, "November"), (12, "December"),
]


def current_reporting_period():
    now = datetime.now()
    return now.month, now.year


def latest_imported_reporting_period():
    latest = db.session.query(MonthlyFigure.year, MonthlyFigure.month).order_by(
        MonthlyFigure.year.desc(),
        MonthlyFigure.month.desc(),
    ).first()
    if latest:
        return int(latest.month), int(latest.year)
    return current_reporting_period()


def selected_reporting_period():
    default_month, default_year = latest_imported_reporting_period()
    try:
        month = int(request.args.get("month", default_month))
    except (TypeError, ValueError):
        month = default_month
    try:
        year = int(request.args.get("year", default_year))
    except (TypeError, ValueError):
        year = default_year
    if month < 1 or month > 12:
        month = default_month
    if year < 2000 or year > 2100:
        year = default_year
    return month, year


def month_label(month, year):
    month_name = dict(MONTHS).get(int(month), str(month))
    return f"{month_name} {year}"


def reporting_years():
    default_month, current_year = current_reporting_period()
    years = [row[0] for row in db.session.query(MonthlyFigure.year).distinct().order_by(MonthlyFigure.year.desc()).all()]
    if current_year not in years:
        years.insert(0, current_year)
    return years





def build_grouped_royalty_row(figures, main_franchise, selected_month, selected_year):
    """Build one combined royalty row for a main franchise user with linked branches.

    The linked branch monthly figures are summed first. The royalty method and
    royalty scale are then read from the main franchise's Franchise Details page.
    This is used only in franchise-user view mode so Admin can still audit each
    individual branch row separately.
    """
    if not figures or not main_franchise:
        return None

    def total(field):
        return sum(Decimal(getattr(item, field, 0) or 0) for item in figures)

    grouped = SimpleNamespace()
    grouped.id = None
    grouped.is_grouped = True
    grouped.source_ids = [item.id for item in figures]
    grouped.source_figures = figures
    grouped.source_branch_names = sorted({
        (getattr(getattr(item, "franchise", None), "business_name", "") or "Unnamed Franchise")
        for item in figures
    })
    grouped.franchise = main_franchise
    grouped.franchise_id = main_franchise.id
    grouped.month = selected_month
    grouped.year = selected_year
    grouped.period_label = f"{selected_year}-{selected_month:02d}"
    grouped.status = "Calculated"

    grouped.funeral_receipts = total("funeral_receipts")
    grouped.society_receipts = total("society_receipts")
    grouped.cash_sales = total("cash_sales")
    grouped.tombstone_receipts = total("tombstone_receipts")
    grouped.obo_service_receipts = total("obo_service_receipts")
    grouped.sales = (
        grouped.funeral_receipts
        + grouped.society_receipts
        + grouped.cash_sales
        + grouped.tombstone_receipts
        + grouped.obo_service_receipts
    )
    grouped.insurance_receipts = total("insurance_receipts")
    grouped.insurance_payover = total("insurance_payover")
    grouped.admin_fee = total("admin_fee")
    grouped.insurance_joinings = sum(int(getattr(item, "insurance_joinings", 0) or 0) for item in figures)
    grouped.mf_files = sum(int(getattr(item, "mf_files", 0) or 0) for item in figures)
    grouped.cash = grouped.sales + grouped.insurance_receipts

    royalty_base, gross_method = calculate_royalty_base(grouped, main_franchise)
    grouped.gross_turnover = royalty_base
    grouped.gross_revenue = royalty_base
    grouped.gross_method = gross_method
    grouped.scale_franchise = main_franchise
    _gross, percentage, royalty_amount, minimum_applied = calculate_royalty(main_franchise, royalty_base)
    grouped.royalty_percentage = percentage
    grouped.royalty_amount = royalty_amount
    grouped.minimum_royalty_applied = minimum_applied
    for item in figures:
        item.included_in_group_main_name = main_franchise.business_name
    return grouped


def build_individual_royalty_row(item):
    """Return a display row calculated only from this franchise's own data."""
    values = {
        column.name: getattr(item, column.name)
        for column in MonthlyFigure.__table__.columns
    }
    row = SimpleNamespace(**values)
    row.franchise = item.franchise
    row.period_label = item.period_label
    row.is_grouped = False
    row.is_grouped_summary = False
    row.sales = sum(Decimal(getattr(row, field, 0) or 0) for field in (
        "funeral_receipts", "society_receipts", "cash_sales",
        "tombstone_receipts", "obo_service_receipts",
    ))
    row.admin_fee = max(
        Decimal(row.insurance_receipts or 0) - Decimal(row.insurance_payover or 0),
        Decimal("0"),
    )
    row.cash = row.sales + Decimal(row.insurance_receipts or 0)
    royalty_base, gross_method = calculate_royalty_base(row, row.franchise)
    row.gross_turnover = royalty_base
    row.gross_revenue = royalty_base
    row.gross_method = gross_method
    _gross, percentage, royalty_amount, minimum_applied = calculate_royalty(row.franchise, royalty_base)
    row.royalty_percentage = percentage
    row.royalty_amount = royalty_amount
    row.minimum_royalty_applied = minimum_applied
    return row


def insert_grouped_summary_rows(figures, selected_month, selected_year):
    if not figures:
        return figures
    rows_by_franchise = {int(item.franchise_id): item for item in figures if getattr(item, "franchise_id", None)}
    grouped_by_main = {}
    linked_to_main = {}
    linked_rows_by_main = {}
    individual_rows_by_franchise = {}
    for group in grouped_franchise_sets(rows_by_franchise.keys()):
        main = group["main"]
        linked = group["linked"]
        linked_rows = [
            build_individual_royalty_row(rows_by_franchise[franchise.id])
            for franchise in linked if franchise.id in rows_by_franchise
        ]
        if len(linked_rows) < 2 or main.id not in rows_by_franchise:
            continue
        grouped = build_grouped_royalty_row(linked_rows, main, selected_month, selected_year)
        if not grouped:
            continue
        individual_rows_by_franchise.update({int(row.franchise_id): row for row in linked_rows})
        grouped.is_grouped_summary = True
        grouped.status = "Grouped Total"
        grouped_by_main[int(main.id)] = grouped
        linked_rows_by_main[int(main.id)] = [
            next(row for row in linked_rows if row.franchise_id == franchise.id)
            for franchise in linked
            if franchise.id != main.id and franchise.id in rows_by_franchise
        ]
        for franchise in linked:
            if franchise.id != main.id:
                linked_to_main[int(franchise.id)] = main.business_name

    output = []
    emitted = set()
    for item in figures:
        franchise_id = int(item.franchise_id)
        if franchise_id in emitted:
            continue
        if franchise_id in linked_to_main:
            continue
        item = individual_rows_by_franchise.get(franchise_id, item)
        output.append(item)
        emitted.add(franchise_id)
        linked_rows = linked_rows_by_main.get(franchise_id, [])
        for linked_item in linked_rows:
            linked_id = int(linked_item.franchise_id)
            linked_item.included_in_group_main_name = item.franchise.business_name if item.franchise else linked_to_main.get(linked_id, "")
            output.append(linked_item)
            emitted.add(linked_id)
        grouped = grouped_by_main.get(franchise_id)
        if grouped:
            output.append(grouped)
    return output


def permission_required(code):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.has_permission(code):
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator



def current_user_role_names():
    return {role.name for role in getattr(current_user, "roles", [])}


def is_franchise_side_user():
    """Users in these roles calculate royalties from their own linked franchises.

    Admin and finance users keep the normal branch-by-branch audit view.
    """
    return bool(current_user_role_names() & {"Franchise User", "Franchise Manager", "Read Only User"})


def get_ordered_linked_franchises_for_user(user):
    if user.id == current_user.id and current_user.is_franchise_scoped_user():
        return current_user.accessible_franchises()
    return ordered_linked_franchises_for_user(user)


def get_user_linked_franchises():
    return get_ordered_linked_franchises_for_user(current_user)


def get_primary_franchise_for_user(user, linked_franchises):
    if not user or not linked_franchises:
        return None
    ordered = ordered_linked_franchises_for_user(user)
    linked_ids = {franchise.id for franchise in linked_franchises}
    for franchise in ordered:
        if franchise.id in linked_ids:
            return franchise
    return linked_franchises[0]


def get_main_franchise_for_group(linked_franchises, selected, group_user=None):
    """Pick the main franchise for grouped royalty calculation.

    The ordered linked-franchise list puts the main Business Name first. That
    branch must drive the gross method and royalty scale for the whole group.
    If no linked branch is available, fall back to the selected branch.
    """
    if not linked_franchises:
        return selected
    primary = get_primary_franchise_for_user(group_user or current_user, linked_franchises)
    if primary:
        return primary
    linked_ids = {franchise.id for franchise in linked_franchises}
    if selected and selected.id in linked_ids:
        return selected
    return linked_franchises[0]



def get_group_user_for_selected_franchise(selected_franchise):
    """Find the franchise-side user group that contains the selected franchise."""
    if not selected_franchise:
        return None
    selected_id = getattr(selected_franchise, "id", None)
    franchise_side_roles = {"Franchise User", "Franchise Manager", "Read Only User"}
    candidates = []
    for user in User.query.all():
        user_roles = {role.name for role in getattr(user, "roles", [])}
        if not (user_roles & franchise_side_roles):
            continue
        linked = get_ordered_linked_franchises_for_user(user)
        if len(linked) < 2:
            continue
        if any(franchise.id == selected_id for franchise in linked):
            primary = get_primary_franchise_for_user(user, linked)
            candidates.append((0 if primary and primary.id == selected_id else 1, user))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def get_figures():
    accessible_franchises = get_accessible_franchises()
    selected = get_selected_franchise()
    selected_month, selected_year = selected_reporting_period()

    linked_franchises = get_user_linked_franchises()
    group_user = None
    franchise_group_mode = is_franchise_side_user() and len(linked_franchises) > 1

    # When Admin/Finance opens a selected main branch that belongs to a
    # franchise-side user with multiple linked branches, show the same grouped
    # royalty calculation for that selected main branch.  All Branches remains an
    # audit view and still shows individual branches.
    if not franchise_group_mode and selected and is_franchise_view_mode():
        group_user = get_group_user_for_selected_franchise(selected)
        if group_user:
            linked_franchises = get_ordered_linked_franchises_for_user(group_user)
            franchise_group_mode = len(linked_franchises) > 1

    show_all_franchises = is_privileged_user() and not is_franchise_view_mode() and not franchise_group_mode

    if franchise_group_mode:
        main_franchise = get_main_franchise_for_group(linked_franchises, selected, group_user or current_user)
        linked_ids = [franchise.id for franchise in linked_franchises]
        linked_figures = MonthlyFigure.query.filter(
            MonthlyFigure.month == selected_month,
            MonthlyFigure.year == selected_year,
            MonthlyFigure.franchise_id.in_(linked_ids),
        ).all()
        # Royalty rows are calculated when figures are imported or deliberately
        # recalculated.  A dashboard visit must never write or recalculate the
        # whole period; that made the overview unusable on a live database.
        individual_figures = [build_individual_royalty_row(item) for item in linked_figures]
        grouped = build_grouped_royalty_row(individual_figures, main_franchise, selected_month, selected_year)
        if grouped:
            grouped.is_grouped_summary = True
            grouped.status = "Grouped Total"
            figures = [grouped] + individual_figures
        else:
            figures = []
        return figures, main_franchise, linked_franchises, False, selected_month, selected_year

    query = MonthlyFigure.query.filter(
        MonthlyFigure.month == selected_month,
        MonthlyFigure.year == selected_year,
    )
    if show_all_franchises:
        accessible_ids = [franchise.id for franchise in accessible_franchises]
        if accessible_ids:
            query = query.filter(MonthlyFigure.franchise_id.in_(accessible_ids))
        else:
            query = query.filter(False)
    elif selected:
        query = query.filter_by(franchise_id=selected.id)

    figures = query.options(joinedload(MonthlyFigure.franchise)).join(
        Franchise, MonthlyFigure.franchise_id == Franchise.id
    ).order_by(
        Franchise.business_name.asc(),
        MonthlyFigure.id.desc(),
    ).all()
    if show_all_franchises:
        figures = insert_grouped_summary_rows(figures, selected_month, selected_year)

    return figures, selected, accessible_franchises, show_all_franchises, selected_month, selected_year


def dashboard_totals(figures):
    grouped_summaries = [item for item in figures if getattr(item, "is_grouped_summary", False)]
    total_rows = grouped_summaries + [
        item for item in figures
        if not getattr(item, "is_grouped_summary", False)
        and not getattr(item, "included_in_group_main_name", None)
    ]
    if not total_rows:
        total_rows = figures
    return {
        "total_due": sum(Decimal(item.royalty_amount or 0) for item in total_rows),
        "unapproved": len([item for item in total_rows if item.status not in ["Royalty Approved", "Royalty Locked"]]),
        "approved": len([item for item in total_rows if item.status == "Royalty Approved"]),
        "locked": len([item for item in total_rows if item.status == "Royalty Locked"]),
    }


@royalties_bp.route("/")
@login_required
@permission_required("royalties:view")
def index():
    figures, selected, accessible_franchises, show_all_franchises, selected_month, selected_year = get_figures()
    totals = dashboard_totals(figures)
    return render_template(
        "royalties/index.html",
        figures=figures,
        totals=totals,
        selected=selected,
        accessible_franchises=accessible_franchises,
        show_all_franchises=show_all_franchises,
        selected_month=selected_month,
        selected_year=selected_year,
        selected_period_label=month_label(selected_month, selected_year),
        month_options=MONTHS,
        year_options=reporting_years(),
    )


@royalties_bp.route("/<int:figure_id>/status", methods=["POST"])
@login_required
@permission_required("royalties:approve")
def update_status(figure_id):
    """Move a royalty row between its working and issued stages.

    Approval and locking deliberately remain separate actions.  Locked rows are
    immutable and cannot be moved back from this screen.
    """
    figure = MonthlyFigure.query.get_or_404(figure_id)
    enforce_franchise_access(figure.franchise_id)
    requested_status = (request.form.get("status") or "").strip()
    if requested_status not in {"Calculated", "Published"}:
        abort(400)
    if figure.status == "Royalty Locked":
        flash("Locked royalty records cannot be changed.", "error")
        return redirect(url_for("royalties.index"))

    figure.status = requested_status
    log_action(
        "Royalties",
        f"Marked royalty calculation as {requested_status}",
        f"Franchise: {figure.franchise.business_name if figure.franchise else figure.franchise_id}; Period: {figure.period_label}",
    )
    db.session.commit()
    flash(f"Royalty calculation marked as {requested_status.lower()}.", "success")
    return redirect(url_for("royalties.index"))


@royalties_bp.route("/<int:figure_id>/approve", methods=["POST"])
@login_required
@permission_required("royalties:approve")
def approve(figure_id):
    figure = MonthlyFigure.query.get_or_404(figure_id)
    enforce_franchise_access(figure.franchise_id)
    figure.status = "Royalty Approved"
    log_action("Royalties", "Approved royalty calculation", f"Period: {figure.period_label}")
    db.session.commit()
    flash("Royalty calculation approved.", "success")
    return redirect(url_for("royalties.index"))


@royalties_bp.route("/<int:figure_id>/lock", methods=["POST"])
@login_required
@permission_required("royalties:approve")
def lock(figure_id):
    figure = MonthlyFigure.query.get_or_404(figure_id)
    enforce_franchise_access(figure.franchise_id)
    figure.status = "Royalty Locked"
    log_action("Royalties", "Locked royalty calculation", f"Period: {figure.period_label}")
    db.session.commit()
    flash("Royalty calculation locked.", "success")
    return redirect(url_for("royalties.index"))


@royalties_bp.route("/export-pdf")
@login_required
@permission_required("royalties:export")
def export_pdf():
    figures, selected, accessible_franchises, show_all_franchises, selected_month, selected_year = get_figures()
    from app.reports.pdf import build_royalty_history_pdf
    period_label = month_label(selected_month, selected_year)

    if show_all_franchises:
        export_franchise = SimpleNamespace(
            business_name="Martin's Funerals South Africa",
            franchise_code="MARTINS-SA",
            pty_number="",
            vat_number="",
            office_address="South Africa",
            office_number="",
            after_hours_number="",
            public_email="",
            franchisee_email="",
            is_company_profile=True,
        )
    else:
        export_franchise = selected or (figures[0].franchise if figures else SimpleNamespace(business_name="Franchise"))

    pdf_path = build_royalty_history_pdf(figures, export_franchise, current_user, period_label=period_label)
    log_action("Royalties", "Exported royalty history PDF", f"{getattr(export_franchise, 'business_name', 'All franchises')} - {period_label}")
    db.session.commit()
    safe_label = period_label.lower().replace(" ", "-")
    franchise_name = getattr(export_franchise, "business_name", None) or "All Franchises"
    safe_franchise = secure_filename(franchise_name).lower() or "all-franchises"
    return send_file(pdf_path, as_attachment=True, download_name=f"royalty-history-{safe_franchise}-{safe_label}.pdf")
