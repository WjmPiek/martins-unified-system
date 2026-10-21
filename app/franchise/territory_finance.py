"""Territory planning calculations, using Unified's existing royalty rules.

Compass fee bands and weights are preserved. Example inputs never constitute
approved Martins assumptions. Forecasts are independent of MonthlyFigure rows.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.royalty_engine import calculate_royalty_amount, select_royalty_method, get_royalty_scales

COMMERCIAL_FEES = {"small":75000,"standard":150000,"large":200000}
COMMERCIAL_LABELS = {"small":"Small area","standard":"Standard area","large":"Large area"}

WEIGHTS = {"population": 20, "demand": 25, "affordability": 15,
           "competition": 15, "policy": 10, "access": 10, "growth": 5}
BANDS = [(90, "Premium", 225000), (75, "High potential", 187500),
         (60, "Standard", 150000), (40, "Emerging", 112500), (0, "Development", 75000)]
DEFAULTS = {"population": 0, "mortality_per_1000": 0, "household_size": 3,
    "market_share": 10, "demand_basis":"territory", "recorded_monthly_services":0, "burial_price": 20000, "burial_cost": 8500,
    "cremation_share": 0, "cremation_price": 0, "cremation_cost": 0,
    "expense_mode":"manual", "expense_percent":70, "fixed_cost": 60000, "owner_salary": 25000, "salary_in_fixed": True,
    "policy_receipts": 0, "policy_payover": 0, "policy_commission": 0,
    "policy_direct_cost": 0, "other_sales": 0,
    "setup": 300000, "working_capital": 180000, "joining_fee": 0,
    "territory_fee": 150000, "fee_method": "scorecard", "commercial_category":"standard", "repayment_months": 60, "interest_percent": 0,
    "payment_mode": "instalments", "volume_growth": 2, "price_growth": 4,
    "cost_growth": 5, "ramp_months": 3, "reserve": 5000,
    "vat_percent": 15, "vat_recoverable": True, "tax_percent": 0,
    "annual_capex": 0, "annual_working": 0, "annual_finance": 0,
    "target_margin": 30, "safety_buffer": 20, "confirmed": False,
    "start_year": 2027, "start_month": 1}


def number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name}: enter a finite number")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError(f"{name}: enter a finite number") from None
    if not result.is_finite() or abs(result) > Decimal("1000000000000"):
        raise ValueError(f"{name}: enter a finite number within the supported range")
    return result


def validate_inputs(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError("Unknown financial inputs")
    a = {**DEFAULTS, **raw}
    for key, value in DEFAULTS.items():
        if isinstance(value, bool):
            if not isinstance(a[key], bool):
                raise ValueError(f"{key}: choose yes or no")
        elif isinstance(value, (int, float)):
            a[key] = number(a[key], key)
            if key.endswith("growth"):
                if not -50 <= a[key] <= 50:
                    raise ValueError(f"{key}: use -50 to 50 percent")
            elif a[key] < 0:
                raise ValueError(f"{key}: cannot be negative")
    for key in ["market_share", "cremation_share", "tax_percent", "target_margin", "safety_buffer", "interest_percent", "expense_percent"]:
        if a[key] > 100:
            raise ValueError(f"{key}: cannot exceed 100 percent")
    for key, low, high in [("repayment_months", 1, 60), ("ramp_months", 0, 60),
                           ("start_year", 2000, 2200), ("start_month", 1, 12)]:
        if a[key] != int(a[key]) or not low <= a[key] <= high:
            raise ValueError(f"{key}: use a whole number from {low} to {high}")
        a[key] = int(a[key])
    if a["household_size"] <= 0 or a["mortality_per_1000"] > 50 or a["vat_percent"] > 30:
        raise ValueError("Check household size, mortality and VAT inputs")
    if a["expense_mode"] == "manual" and a["salary_in_fixed"] and a["owner_salary"] > a["fixed_cost"]:
        raise ValueError("Owner salary exceeds fixed costs said to include it")
    if not isinstance(a["payment_mode"], str) or a["payment_mode"] not in {"instalments", "upfront"}:
        raise ValueError("Choose instalments or upfront payment")
    if not isinstance(a["demand_basis"], str) or a["demand_basis"] not in {"territory", "franchise_history"}:
        raise ValueError("Choose territory research or recorded franchise activity")
    if not isinstance(a["expense_mode"], str) or a["expense_mode"] not in {"manual", "percentage"}:
        raise ValueError("Choose manually entered expenses or a revenue percentage")
    if not isinstance(a['fee_method'], str) or a['fee_method'] not in {'scorecard', 'profit_margin', 'fair_territory', 'commercial'}:
        raise ValueError('Choose the scorecard or optional Territory Fee method')
    if not isinstance(a['commercial_category'],str) or a['commercial_category'] not in COMMERCIAL_FEES:
        raise ValueError('Choose a small, standard or large commercial area category')
    if a['fee_method'] == 'profit_margin': a['fee_method'] = 'fair_territory'  # legacy saved drafts
    return a


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def scorecard(scores, evidence):
    if not isinstance(scores, dict) or not isinstance(evidence, dict) or set(scores) - set(WEIGHTS):
        raise ValueError("Invalid scorecard")
    score = Decimal(0)
    complete = True
    for key, weight in WEIGHTS.items():
        if key not in scores or scores[key] is None or scores[key] == "":
            complete = False
            continue
        value = number(scores[key], key)
        if not 0 <= value <= 5:
            raise ValueError(f"{key}: score must be from 0 to 5")
        score += value / 5 * weight
    documented = complete and all(isinstance(evidence.get(k), str) and evidence[k].strip() for k in WEIGHTS)
    band = next((b for b in BANDS if score >= b[0]), BANDS[-1]) if complete else None
    return {"complete": complete, "documented": bool(documented), "score": score if complete else None,
            "band": band[1] if band else None, "score_fee": Decimal(band[2]) if band else None}


def forecast(franchise, raw, sales_factor=Decimal(1), royalty_cache=None, planning_royalty=None):
    a = validate_inputs(raw)
    royalty_cache = {} if royalty_cache is None else royalty_cache
    monthly_demand = a["population"] * a["mortality_per_1000"] / 1000 / 12 * a["market_share"] / 100
    if a["demand_basis"] == "franchise_history":
        monthly_demand = a["recorded_monthly_services"]
    rows, errors = [], set()
    fee_factor = Decimal(1) if a["vat_recoverable"] else 1 + a["vat_percent"] / 100
    principal = money(a["territory_fee"] * fee_factor)
    balance = principal
    rate = a["interest_percent"] / 1200
    term = a["repayment_months"]
    payment = money(principal * rate / (1 - (1 + rate) ** -term)) if rate else money(principal / term)
    initial = money(a["setup"] + a["working_capital"] + a["joining_fee"])
    if a["payment_mode"] == "upfront":
        initial += principal
        balance = Decimal(0)
    cash_balance = -initial
    minimum_balance = cash_balance
    payback = None
    if planning_royalty is not None:
        planning_royalty=number(planning_royalty,'Planning royalty')
        if not 0<=planning_royalty<=100:raise ValueError('Planning royalty must be from 0 to 100 percent')
    economic_key=('economics',planning_royalty,sales_factor,tuple((key,str(value)) for key,value in sorted(a.items()) if key!='territory_fee'))
    economics=royalty_cache.setdefault(economic_key,{})
    for month in range(60):
        if month not in economics:
            year = month // 12
            ramp = min(Decimal(1), Decimal(month + 1) / a["ramp_months"]) if a["ramp_months"] else Decimal(1)
            volume = monthly_demand * (1 + a["volume_growth"] / 100) ** year * ramp * sales_factor
            price_growth = (1 + a["price_growth"] / 100) ** year
            cost_growth = (1 + a["cost_growth"] / 100) ** year
            cremations = volume * a["cremation_share"] / 100
            burials = volume - cremations
            services = (burials * a["burial_price"] + cremations * a["cremation_price"]) * price_growth
            other_sales = a["other_sales"] * price_growth * sales_factor
            premiums = a["policy_receipts"] * sales_factor
            payover = a["policy_payover"] * sales_factor
            commission = a["policy_commission"] * sales_factor
            admin = premiums - payover
            revenue = money(services + other_sales + admin + commission)
            absolute_month = a["start_year"] * 12 + a["start_month"] - 1 + month
            calendar_year, calendar_month = absolute_month // 12, absolute_month % 12 + 1
            if planning_royalty is None:
                method, _, warnings, method_errors = select_royalty_method(franchise, period_month=calendar_month, period_year=calendar_year)
            else:method,warnings,method_errors='new',[],[]
            errors.update(method_errors)
            # Match Unified sales + insurance/admin basis; never create a MonthlyFigure.
            royalty_base = max(Decimal(0), services + other_sales + commission + (admin if method == "new" else premiums))
            if planning_royalty is None:
                if royalty_base not in royalty_cache:
                    royalty_cache[royalty_base] = calculate_royalty_amount(franchise, royalty_base)
                _, royalty, _, _, _, _, scale_warnings, scale_errors = royalty_cache[royalty_base]
            else:royalty,scale_warnings,scale_errors=money(royalty_base*planning_royalty/100),[],[]
            royalty = money(royalty)
            errors.update(scale_errors)
            errors.update(warnings)
            errors.update(scale_warnings)
            direct = money((burials * a["burial_cost"] + cremations * a["cremation_cost"] + a["policy_direct_cost"] * sales_factor) * cost_growth)
            overhead = money((a["fixed_cost"] + (0 if a["salary_in_fixed"] else a["owner_salary"])) * cost_growth)
            if a['expense_mode']=='percentage':
                # Total operating-expense ratio includes the contractual royalty.
                # Display royalty separately, allocating only the balance to other costs.
                direct=Decimal(0)
                expense_budget=money(max(Decimal(0),revenue)*a['expense_percent']/100)
                overhead=max(Decimal(0),expense_budget-royalty)
                if royalty>expense_budget:
                    errors.add('Contractual royalty exceeds the total operating-expense assumption; review the expense percentage')
            operating = revenue - direct - royalty - overhead
            tax = money(max(Decimal(0), operating) * a["tax_percent"] / 100)
            adjustments = money(tax + (a["annual_capex"] + a["annual_working"] + a["annual_finance"]) / 12)
            reserve = money(max(Decimal(0), a["reserve"] * cost_growth - adjustments))
            free = operating - adjustments - reserve
            economics[month]=(volume, revenue, direct, royalty, overhead, operating, tax, adjustments, reserve, free,tuple(errors))
        volume, revenue, direct, royalty, overhead, operating, tax, adjustments, reserve, free,cached_errors=economics[month]
        errors.update(cached_errors)
        interest = money(balance * rate) if month < term else Decimal(0)
        repayment = Decimal(0)
        if balance > 0 and month < term:
            repayment = balance + interest if month == term - 1 else min(balance + interest, payment)
            balance = money(balance + interest - repayment)
        retained = free - repayment
        cash_balance += retained
        minimum_balance = min(minimum_balance, cash_balance)
        if payback is None and cash_balance >= 0:
            payback = month + 1
        rows.append(dict(month=month + 1, services=volume, revenue=money(revenue), direct=money(direct),
            royalty=money(royalty), overhead=money(overhead), operating=money(operating), tax=money(tax),
            adjustments=money(adjustments), reserve=money(reserve), cash_deductions=money(adjustments-tax+reserve), free=money(free),
            repayment=money(repayment), interest=interest, principal_balance=balance,
            retained=money(retained), cumulative=money(cash_balance)))
    years = []
    for year in range(5):
        group = rows[year * 12:(year + 1) * 12]
        years.append({"year": year + 1, **{key: sum(r[key] for r in group) for key in
            ["services", "revenue", "direct", "royalty", "overhead", "operating", "tax", "cash_deductions", "repayment", "interest", "retained"]}})
    cash_available = sum(r["free"] for r in rows)
    capacity = max(Decimal(0), (cash_available - (a["setup"] + a["working_capital"] + a["joining_fee"])) / fee_factor)
    # Short-term affordability: opening working capital must cover every monthly
    # operating/repayment deficit, in addition to eventual investment recovery.
    liquid = a["working_capital"]
    minimum_liquid = liquid
    for row in rows:
        liquid += row["retained"]
        minimum_liquid = min(minimum_liquid, liquid)
    return dict(months=rows, years=years, errors=sorted(errors), capacity=money(capacity),
        initial=money(initial), ending_cash=money(cash_balance), payback_months=payback,
        minimum_liquid=money(minimum_liquid), additional_funding=max(Decimal(0), -money(minimum_liquid)),
        affordable=minimum_liquid >= 0 and cash_balance >= 0 and not errors,
        projected_households=a["population"] / a["household_size"],
        annual_deaths=a["population"] * a["mortality_per_1000"] / 1000)


def profit_margin_fee(years):
    """Owner-requested option: sum annual positive operating profit times its margin.

    Operating profit includes monthly royalties and operating costs, but precedes
    tax, reserves, financing and territory repayments to avoid a circular price.
    No positive fee is generated by a loss or zero/non-positive turnover.
    """
    rows = []
    previous = None
    for year in years:
        turnover = Decimal(str(year['revenue']))
        profit = Decimal(str(year['operating']))
        margin = profit / turnover if turnover > 0 else None
        contribution = money(profit * margin) if turnover > 0 and profit > 0 else Decimal('0.00')
        rows.append(dict(year=year['year'], turnover=money(turnover), profit=money(profit),
            margin_percent=margin * 100 if margin is not None else None,
            turnover_growth_percent=(turnover / previous - 1) * 100 if previous is not None and previous > 0 else None,
            fee_contribution=contribution))
        previous = turnover
    return dict(method='profit_margin', years=rows, total=sum((r['fee_contribution'] for r in rows), Decimal('0.00')),
        formula='Annual contribution = positive operating profit × (operating profit ÷ turnover); total = sum of five annual contributions.',
        profit_basis='Operating profit after direct costs, overheads, owner salary where included and monthly royalties; before tax, reserves, financing and territory repayments.',
        turnover_basis='Forecast business income: funeral services and other sales plus net policy administration income/commission. Premiums paid over to an insurer are excluded.',
        provenance='Owner-requested planning method, confirmed 21 October 2026; not verified as a FASA-prescribed formula.',
        fasa_source='https://www.fasa.co.za/assessments/DDSelf-AssessmentChecklist.pdf',
        fasa_checked_at='2026-10-21', confidence='planning assumption',
        status='Illustrative fee calculation; separate affordability and evidence checks still apply. Amount excludes VAT; no present-value discount is applied. Contributions use unrounded margins; displayed percentages are rounded.')


def affordability_checks(base, stress, a, owner_budget=False):
    # Twelve rounded monthly expense totals can differ from an annual target
    # by at most a cent per month. Do not reject the intended 30% on cents.
    checks = dict(monthly_liquidity=base['minimum_liquid'] >= 0 and stress['minimum_liquid'] >= 0,
        five_year_recovery=base['ending_cash'] >= 0 and stress['ending_cash'] >= 0,
        target_margin=all(y['revenue'] > 0 and y['operating'] + Decimal('0.12') >= money(y['revenue'] * a['target_margin'] / 100) for y in base['years']),
        downside_buffer=stress['ending_cash'] >= stress['initial'] * a['safety_buffer'] / 100,
        royalty_setup=not base['errors'] and not stress['errors'])
    if owner_budget:
        # The percentage budget claims to include owner compensation. It must at
        # least cover the entered salary after the separately displayed royalty.
        checks['owner_salary_budget'] = a['expense_mode'] != 'percentage' or all(
            row['overhead'] >= money(a['owner_salary'] * (1 + a['cost_growth'] / 100) ** (i // 12))
            for scenario in [base, stress] for i, row in enumerate(scenario['months']))
    return checks


def affordable_territory_ceiling(franchise, inputs, a, royalty_cache, planning_royalty=None):
    """Largest whole-Rand fee passing both scenarios, independent of score bands."""
    def scenarios(fee):
        candidate = {**inputs, 'territory_fee': fee}
        base = forecast(franchise, candidate, royalty_cache=royalty_cache, planning_royalty=planning_royalty)
        stress = forecast(franchise, candidate, Decimal('0.8'), royalty_cache, planning_royalty=planning_royalty)
        return base, stress, affordability_checks(base, stress, a, owner_budget=True)
    base, stress, checks = scenarios(0)
    labels = dict(monthly_liquidity='Monthly cash runs short even without a territory fee.',
        five_year_recovery='The investment is not recovered within five years even without a territory fee.',
        target_margin='The entered target operating margin is not achieved in every projected year.',
        downside_buffer='The 20% lower-sales scenario does not retain the entered investment safety buffer.',
        royalty_setup='Complete or correct the contractual royalty and operating-cost assumptions.',
        owner_salary_budget='The percentage expense budget cannot cover the entered owner salary in every month of both scenarios.')
    reasons = [labels[key] for key, passed in checks.items() if not passed]
    if not checks['royalty_setup']:
        return dict(ceiling=None, checks=checks, reasons=reasons, status='unavailable')
    if not all(checks.values()):
        return dict(ceiling=Decimal(0), checks=checks, reasons=reasons, status='no_positive_fee')
    # Aggregate cash provides an upper bound; monthly tests, finance charges and
    # the downside buffer can only reduce it. Search whole Rands conservatively.
    low, high = 0, min(1000000000000, int(base['capacity']), int(stress['capacity']))
    while low < high:
        mid = (low + high + 1) // 2
        _, _, candidate_checks = scenarios(mid)
        if all(candidate_checks.values()): low = mid
        else: high = mid - 1
    return dict(ceiling=Decimal(low), checks=checks, reasons=[], status='calculated',
        rounding='Affordable ceiling rounded down to a whole Rand; fee search limited to R1 trillion.')


def calculate(franchise, inputs, scores, evidence, planning_royalty=None):
    a = validate_inputs(inputs)
    card = scorecard(scores, evidence)
    royalty_cache = {}
    expected = forecast(franchise, inputs, royalty_cache=royalty_cache, planning_royalty=planning_royalty)
    downside = forecast(franchise, inputs, Decimal("0.8"), royalty_cache, planning_royalty=planning_royalty)
    profit_fee = profit_margin_fee(expected['years'])
    profit_fee['selected'] = a['fee_method'] == 'fair_territory'
    affordability = affordable_territory_ceiling(franchise, inputs, a, royalty_cache, planning_royalty=planning_royalty)
    ceiling = affordability['ceiling']
    fair_fee = min(profit_fee['total'], ceiling) if ceiling is not None else None
    profit_fee.update(fair_method_version='martins-fair-territory-v1', affordable_ceiling=ceiling,
        fair_fee=fair_fee, reduction=profit_fee['total']-fair_fee if fair_fee is not None else None,
        affordability=affordability,
        fair_formula='Optional Territory Fee = lower of five-year profit-margin valuation and affordable fee ceiling.',
        demand_basis=dict(population=a['population'], annual_deaths=expected['annual_deaths'],
            market_share=a['market_share'], first_year_services=expected['years'][0]['services']),
        fairness_note='Provisional under the entered assumptions. Population and mortality affect achievable services; geography alone carries no fee premium. Territory size is assessed through earning potential. Confirm local competition, service capacity, travel costs, tax and owner compensation before approving the fee.')
    recommended = None
    reason = "Confirm assumptions, document all scores and complete royalty setup."
    commercial_amount=Decimal(COMMERCIAL_FEES[a['commercial_category']])
    commercial=dict(policy_version='martins-commercial-v1',category=a['commercial_category'],label=COMMERCIAL_LABELS[a['commercial_category']],
        amount=commercial_amount,rates=COMMERCIAL_FEES,selected=a['fee_method']=='commercial',
        affordable=ceiling is not None and commercial_amount<=ceiling,
        difference=abs(fair_fee-commercial_amount) if fair_fee is not None else None,
        comparison='unavailable' if fair_fee is None else 'below' if commercial_amount<fair_fee else 'above' if commercial_amount>fair_fee else 'equal',
        source='Original Franchise Compass small/standard/large commercial fee structure, reaffirmed by the owner on 21 October 2026.')
    if a['fee_method']=='commercial':
        reason='Martins commercial fee selected. Review source evidence, owner salary, cash flow and repayment affordability before offering it.'
        if ceiling is None or commercial_amount>ceiling:
            reason='The commercial fee exceeds the affordable ceiling under the entered assumptions; review the fairness comparison and funding before offering it.'
        elif card['documented'] and a['confirmed'] and not expected['errors']:
            recommended=commercial_amount
            reason='Selected Martins commercial fee is within the affordable ceiling; evidence and owner confirmation are recorded.'
    elif a['fee_method'] == 'fair_territory':
        if fair_fee is None:
            reason = 'The affordable territory-fee ceiling cannot be established until calculation errors are resolved.'
        elif fair_fee <= 0:
            reason = 'The cash-flow, owner compensation and affordability checks do not support a positive territory fee.'
        elif card['documented'] and a['confirmed'] and not expected['errors']:
            recommended = fair_fee
            reason = 'Lower of the five-year profit-margin valuation and the affordable ceiling; monthly cash, owner salary budget, five-year recovery and downside checks pass.'
    elif card["documented"] and a["confirmed"] and not expected["errors"]:
        # Find the largest whole-Rand price passing the actual 60-month forecast
        # in both scenarios. Never infer affordability from aggregate profit alone.
        low, high = 0, int(card["score_fee"])
        while low < high:
            mid = (low + high + 1) // 2
            candidate = {**inputs, "territory_fee": mid}
            base = forecast(franchise, candidate, royalty_cache=royalty_cache, planning_royalty=planning_royalty)
            stress = forecast(franchise, candidate, Decimal("0.8"), royalty_cache, planning_royalty=planning_royalty)
            margin_ok = all(y["revenue"] > 0 and y["operating"] >= money(y["revenue"]*a["target_margin"]/100) for y in base["years"])
            buffer_ok = stress["ending_cash"] >= stress["initial"] * a["safety_buffer"] / 100
            if base["affordable"] and stress["affordable"] and margin_ok and buffer_ok:
                low = mid
            else:
                high = mid - 1
        if low > 0:
            recommended = low
            reason = "Score-band limit, monthly liquidity, five-year recovery, target margin and downside buffer all pass."
        else:
            reason = "The cash-flow and margin checks do not support a positive territory price."
    if planning_royalty is not None:
        policy=dict(is_planning=True,agreement_start=None,agreement_end=None,minimum='0',minimum_is_none=True,
            scale_source_id=None,scale_source='Prospective planning assumption; no existing franchise contract used',
            scales=[{'from':Decimal(0),'to':Decimal('999999999999'),'percent':number(planning_royalty,'Planning royalty')}])
    else:
        scales, scale_source, scale_label = get_royalty_scales(franchise)
        policy = {"agreement_start": str(franchise.agreement_start_date or ""),
            "agreement_end": str(franchise.agreement_end_date or ""),
            "minimum": str(franchise.minimum_royalty_amount), "minimum_is_none": franchise.minimum_royalty_is_none,
            "scale_source_id": getattr(scale_source, "id", None), "scale_source": scale_label,
            "scales": [{"from": s.amount_from, "to": s.amount_to, "percent": s.percentage} for s in scales]}
    return dict(formula_version="martins-territory-v4", fee_method=a["fee_method"], commercial_fee=commercial, profit_margin_fee=profit_fee, royalty_policy=policy, scorecard=card, expected=expected,
                downside=downside, recommended_fee=recommended, recommendation_reason=reason)


def serializable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serializable(item) for item in value]
    return value
