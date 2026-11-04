"""Shared definition of the dashboard's recent monthly-figure activity."""
from sqlalchemy import func, or_, tuple_

from app.extensions import db
from app.models import MonthlyFigure

ACTIVITY_FIELDS = (
    'funeral_receipts', 'claim_receipts', 'society_receipts', 'cash_sales',
    'tombstone_receipts', 'obo_service_receipts', 'insurance_receipts',
    'insurance_payover', 'admin_fee', 'gross_turnover', 'gross_revenue',
    'royalty_amount', 'insurance_joinings', 'mf_files', 'number_of_funerals',
    'number_of_policies', 'number_of_claims',
)


def previous_periods(year, month, count=6):
    periods = set()
    for _ in range(count):
        periods.add((year, month))
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    return periods


def monthly_figure_has_data(figure):
    return any(getattr(figure, field, 0) or 0 for field in ACTIVITY_FIELDS)


def recent_franchise_activity(franchise_ids):
    """Return active IDs and the same six-month label used by Business Overview."""
    latest = db.session.query(MonthlyFigure.year, MonthlyFigure.month).order_by(
        MonthlyFigure.year.desc(), MonthlyFigure.month.desc()
    ).first()
    if not latest:
        return set(), 'No monthly figures imported yet'
    label = f'Last 6 months ending {latest.year}-{latest.month:02d}'
    if not franchise_ids:
        return set(), label
    rows = db.session.query(MonthlyFigure.franchise_id).filter(
        MonthlyFigure.franchise_id.in_(franchise_ids),
        tuple_(MonthlyFigure.year, MonthlyFigure.month).in_(sorted(previous_periods(latest.year, latest.month))),
        or_(*(func.coalesce(getattr(MonthlyFigure, field), 0) != 0 for field in ACTIVITY_FIELDS)),
    ).distinct().all()
    return {row.franchise_id for row in rows}, label
