from decimal import Decimal
import json
import re
from types import SimpleNamespace

from app.extensions import db
from app.models import Franchise, MonthlyFigure, User, user_franchises


# Only the owner-level Franchise User defines a billing group. Managers and
# read-only users can share branch access, but their links must never create a
# second royalty group for the same branches.
FRANCHISE_SIDE_ROLES = {"Franchise User"}
SUM_MONEY_FIELDS = (
    "funeral_receipts",
    "claim_receipts",
    "society_receipts",
    "cash_sales",
    "tombstone_receipts",
    "obo_service_receipts",
    "insurance_receipts",
    "insurance_payover",
)
SUM_COUNT_FIELDS = ("insurance_joinings", "mf_files", "number_of_funerals")


def _identity_key(value):
    text = (value or "").strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(martins?|funerals?|franchise|branch|user|system|pty|ltd)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _user_identity_keys(user):
    values = [
        getattr(user, "business_name", None),
        getattr(user, "full_name", None),
        getattr(user, "name", None),
        getattr(user, "username", None),
    ]
    email = getattr(user, "email", None)
    if email and "@" in email:
        values.append(email.split("@", 1)[0])
    keys = {_identity_key(value) for value in values if value}
    return {key for key in keys if len(key) >= 3}


def _identity_matched_main_franchise(user, linked):
    user_keys = _user_identity_keys(user)
    if not user_keys:
        return None
    for franchise in linked:
        franchise_key = _identity_key(getattr(franchise, "business_name", None))
        if not franchise_key:
            continue
        if franchise_key in user_keys:
            return franchise
        if any(len(key) >= 5 and (franchise_key in key or key in franchise_key) for key in user_keys):
            return franchise
    return None


def _ordered_linked_franchises_for_user(user):
    linked = list(getattr(user, "assigned_franchises", []) or [])
    if not linked:
        return []
    primary_id = db.session.execute(
        db.select(user_franchises.c.franchise_id)
        .where(user_franchises.c.user_id == user.id)
        .where(user_franchises.c.is_primary == True)
    ).scalar()
    linked_sorted = sorted(linked, key=lambda item: item.business_name or "")
    # The grouped-franchise import writes the contractual main branch to
    # user_franchises.is_primary. That explicit setting is authoritative.
    if primary_id:
        primary = [item for item in linked_sorted if item.id == primary_id]
        rest = [item for item in linked_sorted if item.id != primary_id]
        return primary + rest
    # Identity matching is retained only for legacy links that pre-date the
    # is_primary flag.
    identity_main = _identity_matched_main_franchise(user, linked_sorted)
    if identity_main:
        rest = [item for item in linked_sorted if item.id != identity_main.id]
        return [identity_main] + rest
    return linked_sorted


def ordered_linked_franchises_for_user(user):
    return _ordered_linked_franchises_for_user(user)


def grouped_franchise_sets(touched_franchise_ids=None):
    touched = {int(item) for item in (touched_franchise_ids or []) if item}
    groups = []
    claimed_franchise_ids = set()
    users = User.query.all()
    for user in users:
        role_names = {role.name for role in getattr(user, "roles", [])}
        if not (role_names & FRANCHISE_SIDE_ROLES):
            continue
        linked = _ordered_linked_franchises_for_user(user)
        if len(linked) < 2:
            continue
        linked_ids = {franchise.id for franchise in linked}
        if touched and not (linked_ids & touched):
            continue
        # Corrupt/legacy access links can place one branch under more than one
        # owner. Never bill it twice; the first explicit owner group wins.
        if linked_ids & claimed_franchise_ids:
            continue
        groups.append({"user": user, "main": linked[0], "linked": linked})
        claimed_franchise_ids.update(linked_ids)
    return groups


def _money_total(rows, field):
    return sum((Decimal(getattr(row, field, 0) or 0) for row in rows), Decimal("0"))


def _count_total(rows, field):
    return sum(int(getattr(row, field, 0) or 0) for row in rows)


def _append_note(row, note):
    existing = (getattr(row, "notes", "") or "").strip()
    if note in existing:
        return
    row.notes = f"{existing}\n{note}".strip() if existing else note


def _sync_grouped_snapshot(row, main, result, *, is_main):
    """Add grouped-summary audit data without replacing the branch result.

    A monthly figure and its snapshot always belong to one franchise.  The
    grouped result is a separate, derived summary, so it is recorded only in
    diagnostics and must never overwrite that franchise's percentage or amount.
    """
    from app.models import RoyaltyCalculationSnapshot

    snapshot = RoyaltyCalculationSnapshot.query.filter_by(monthly_figure_id=row.id).first()
    if not snapshot:
        return

    diagnostics = {}
    try:
        diagnostics = json.loads(snapshot.diagnostics_json or "{}")
    except (TypeError, ValueError):
        diagnostics = {}
    diagnostics.update({
        "grouped_royalty": True,
        "group_main_franchise_id": main.id,
        "group_main_franchise_name": main.business_name,
        "group_role": "main" if is_main else "linked_branch",
        "group_summary": {
            "royalty_method": result.method,
            "royalty_base": str(result.royalty_base),
            "royalty_percentage": str(result.royalty_percentage),
            "royalty_amount": str(result.royalty_amount),
            "minimum_royalty_amount": str(result.minimum_royalty_amount),
            "minimum_royalty_applied": bool(result.minimum_royalty_applied),
            "scale_source_franchise_id": result.scale_source_franchise_id,
            "scale_source_franchise_name": result.scale_source_franchise_name or "",
            "warnings": result.warnings,
            "blocking_errors": result.blocking_errors,
        },
    })
    snapshot.diagnostics_json = json.dumps(diagnostics, default=str)


def apply_grouped_royalties_for_period(month, year, touched_franchise_ids=None):
    """Calculate a separate grouped summary for each franchise-user group.

    Franchise grouping is controlled by user_franchises.is_primary.  The primary
    linked franchise is the main Business Name, and its royalty method/scale are
    used for the combined monthly figures of every linked branch in that group.
    Individual monthly rows remain calculated with their own franchise data and
    scale; the combined result is never persisted into the main branch row.
    """
    from app.royalty_engine import calculate_monthly_figure

    updated = 0
    grouped = 0
    for group in grouped_franchise_sets(touched_franchise_ids):
        main = group["main"]
        linked = group["linked"]
        linked_ids = [franchise.id for franchise in linked]
        rows = MonthlyFigure.query.filter(
            MonthlyFigure.month == month,
            MonthlyFigure.year == year,
            MonthlyFigure.franchise_id.in_(linked_ids),
        ).all()
        if not rows:
            continue

        grouped_row = SimpleNamespace(
            franchise=main,
            franchise_id=main.id,
            month=month,
            year=year,
        )
        for field in SUM_MONEY_FIELDS:
            setattr(grouped_row, field, _money_total(rows, field))
        for field in SUM_COUNT_FIELDS:
            setattr(grouped_row, field, _count_total(rows, field))
        grouped_row.number_of_funerals = int(getattr(grouped_row, "mf_files", 0) or getattr(grouped_row, "number_of_funerals", 0) or 0)
        result = calculate_monthly_figure(grouped_row)
        grouped += 1
        for row in rows:
            _sync_grouped_snapshot(row, main, result, is_main=row.franchise_id == main.id)
            updated += 1
    return {"groups": grouped, "rows": updated}
