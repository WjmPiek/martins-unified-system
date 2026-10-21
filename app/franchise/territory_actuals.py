"""Read-only, franchise-scoped planning baselines from approved monthly figures."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from app.models import MonthlyFigure
from app.franchise.territory_finance import money, serializable

APPROVED = {"Approved", "Locked", "Royalty Approved", "Royalty Locked"}
FIELDS = ("funeral_receipts", "society_receipts", "cash_sales", "tombstone_receipts", "obo_service_receipts",
          "insurance_receipts", "insurance_payover", "admin_fee", "number_of_funerals", "gross_revenue", "royalty_amount")


def baseline(franchise_id, today=None):
    today = today or datetime.now(timezone(timedelta(hours=2))).date()
    end = today.year*12 + today.month-1
    start = end-12
    candidates = MonthlyFigure.query.filter(MonthlyFigure.franchise_id == franchise_id,
        MonthlyFigure.status.in_(APPROVED), MonthlyFigure.year.between(start//12, (end-1)//12)).order_by(MonthlyFigure.year, MonthlyFigure.month, MonthlyFigure.id).all()
    rows = [r for r in candidates if 1 <= r.month <= 12 and start <= r.year*12+r.month-1 < end]
    seen, source_rows = set(), []
    for row in rows:
        if row.period_label in seen:
            raise ValueError("Duplicate approved monthly figures require review before using franchise history")
        seen.add(row.period_label)
        source_rows.append({"id":row.id, "period":row.period_label, "status":row.status,
            "updated_at":row.updated_at.isoformat() if row.updated_at else None,
            **{k:str(getattr(row,k) or 0) for k in FIELDS}})
    fingerprint = hashlib.sha256(json.dumps({"franchise_id":franchise_id,"window":end,"rows":source_rows},sort_keys=True).encode()).hexdigest()
    count = len(rows)
    totals = {k:sum((Decimal(str(getattr(r,k) or 0)) for r in rows), Decimal(0)) for k in FIELDS}
    funerals = totals["number_of_funerals"]
    proposed = {}
    warnings = ["Monthly Figures stores receipts and funeral counts. The 70% total operating-expense ratio (including royalties) and Compass 60-month, zero-interest territory repayments are planning assumptions, not recorded actual costs or repayment terms.",
        "Recorded funeral counts cover the existing franchise business. They do not prove demand in an expanded or replacement territory."]
    if count < 12: warnings.append(f"Only {count} of the last 12 completed months have approved records; missing months are not treated as zero.")
    if count >= 3 and funerals > 0 and all(value >= 0 for value in totals.values()):
        service_sales = sum(totals[k] for k in ("funeral_receipts","society_receipts","cash_sales"))
        proposed = {"demand_basis":"territory", "recorded_monthly_services":float(funerals/count),
            "burial_price":float(money(service_sales/funerals)), "cremation_share":0,
            "other_sales":float(money((totals['tombstone_receipts']+totals['obo_service_receipts'])/count)),
            "policy_receipts":float(money(totals['insurance_receipts']/count)),
            "policy_payover":float(money(totals['insurance_payover']/count)), "policy_commission":0, "ramp_months":0,
            "expense_mode":"percentage", "expense_percent":70, "payment_mode":"instalments", "repayment_months":60,
            "interest_percent":0, "confirmed":False}
        warnings.append("Service price is blended funeral/society/cash-sale receipts divided by recorded funerals. Burial and cremation mix is not separately recorded; review the mix and exclude non-service cash sales if needed.")
        if totals['admin_fee'] != totals['insurance_receipts']-totals['insurance_payover']:
            warnings.append("Recorded admin fees differ from insurance receipts less payover; reconcile this difference before confirming the forecast.")
    else: warnings.append("At least three approved months, positive recorded funeral counts and non-negative figures are needed to propose a baseline.")
    return {"franchise_id":franchise_id, "fingerprint":fingerprint, "checked_at":datetime.now(timezone.utc).isoformat(),
        "window_start":f'{start//12:04d}-{start%12+1:02d}', "window_end":f'{(end-1)//12:04d}-{(end-1)%12+1:02d}',
        "observed_months":count, "coverage_months":12, "source_rows":source_rows, "totals":serializable(totals),
        "proposed_inputs":proposed, "warnings":warnings, "confidence":"high" if count==12 else "medium" if count>=3 else "unverified",
        "confidence_reason":"Confidence describes the approved internal records, not future profitability or territory-specific demand"}
