"""Area-only prospective planning; never reads a holder's records or contract."""
from datetime import datetime,timezone
from decimal import Decimal
from app.franchise.territory_finance import DEFAULTS
from app.franchise.territory_fields import LABELS,field_value


def suggested_inputs():
    return {**DEFAULTS,'market_share':5,'demand_basis':'territory','recorded_monthly_services':0,
        'expense_mode':'percentage','expense_percent':70,'salary_in_fixed':False,'fixed_cost':35000,
        'policy_receipts':0,'policy_payover':0,'policy_commission':0,'policy_direct_cost':0,'other_sales':0,
        'fee_method':'commercial','commercial_category':'standard','territory_fee':150000,'confirmed':False}


def assumption_ledger(payload,area,research=None):
    defaults=suggested_inputs();rows=[];checked=datetime.now(timezone.utc).isoformat()
    records={r['key']:r for r in (area or {}).get('records',[])}
    for key,value in payload['inputs'].items():
        if key not in LABELS or key in {'confirmed','salary_in_fixed','recorded_monthly_services'}:continue
        row=dict(key=key,label=LABELS[key],value=field_value(key,value),confidence='unverified',status='planning_assumption',
            checked_at=checked,source=None,basis='Compass planning example; confirm costs, tax and proposed agreement terms for this new business.')
        original=defaults.get(key)
        try:edited=Decimal(str(value))!=Decimal(str(original))
        except Exception:edited=value!=original
        if edited:row['basis']='User-entered planning assumption for this prospective business; not an existing holder record.'
        if key in records:
            item=records[key];row.update(confidence=item['confidence'],status='area_estimate',source=(item.get('sources') or [None])[0],
                checked_at=item.get('retrieved_at'),basis=item['method']+'; '+item.get('data_period',''))
        elif key=='household_size' and area:
            row.update(confidence='low',status='area_estimate',source=area['household_source'],basis='Census 2022 provincial household-size proxy; not a suburb household census.')
        elif key in {'policy_receipts','policy_payover','policy_commission','policy_direct_cost','other_sales'} and not edited:
            row['basis']='R0 placeholder: no confirmed policy book or other sales for this prospective business. No holder receipts were imported.'
        elif key=='market_share' and not edited:row['basis']='Illustrative 5% capture assumption for a new operator; not measured local market share.'
        elif key=='expense_percent' and not edited:row['basis']='Owner-requested 70% total operating-expense assumption, including monthly royalties and owner salary.'
        elif key in {'burial_price','cremation_price'}:
            service='burial' if key=='burial_price' else 'cremation'
            selected=(research or {}).get('selected_service_prices',{}).get(service)
            if selected and selected.get('used_in_forecast'):
                row.update(source=selected['source'],confidence='low',status='public_price_candidate',checked_at=selected.get('checked_at'),
                    basis='Selected public cash-package candidate; confirm availability, location and package inclusions. No holder service price used.')
            elif not edited:row['basis']='Compass cash-service example, not a verified local quote. Research local cash packages or enter a supported price.'
        rows.append(row)
    rows.append(dict(key='planning_royalty',label='Suggested royalty expense',value=field_value('interest_percent',payload['prospect']['royalty_percent']),
        confidence='unverified',status='planning_assumption',checked_at=checked,source=None,
        basis='Editable planning royalty assumption. No existing franchise agreement, scale or minimum royalty is used.'))
    return dict(version='prospective-area-v1',name=payload['prospect']['name'],rows=rows,
        note='Area research and explicitly labelled assumptions only. Existing-holder income, policies, service prices, activity and contract terms are not used.')


def running_concern_summary(actuals,business_name=None,franchise=None):
    count=actuals['observed_months'];totals=actuals['totals']
    def value(key):return Decimal(str(totals.get(key,0)))
    receipts=sum(value(key) for key in ['funeral_receipts','society_receipts','cash_sales','tombstone_receipts','obo_service_receipts'])
    income=receipts+value('insurance_receipts')-value('insurance_payover')
    summary={**actuals,'business_name':business_name,'monthly_business_income':str(income/count) if count else None,
        'monthly_services':str(value('number_of_funerals')/count) if count else None,
        'monthly_royalties':str(value('royalty_amount')/count) if count else None,
        'comparison_only':True,'note':'Approved recorded receipts and activity belong to this existing business; they do not establish demand in a replacement or expanded area. Missing months are not zero.',
        'valuation':None,'valuation_note':'Insufficient approved records to calculate a running-concern territory fee.'}
    if franchise is not None and actuals.get('proposed_inputs'):
        from app.franchise.territory_finance import calculate,serializable
        inputs={**suggested_inputs(),**actuals['proposed_inputs'],
            'demand_basis':'franchise_history','setup':0,'working_capital':0,'joining_fee':0,
            'volume_growth':0,'price_growth':0,'cost_growth':0,'ramp_months':0,
            'fee_method':'fair_territory','territory_fee':0,'confirmed':False}
        result=calculate(franchise,inputs,{}, {})
        fair=result['profit_margin_fee']['fair_fee']
        if fair is not None:
            inputs['territory_fee']=float(fair)
            result=calculate(franchise,inputs,{}, {})
        for scenario in ('expected','downside'):result[scenario].pop('months',None)
        summary['valuation']=serializable(dict(inputs=inputs,fee=result['profit_margin_fee'],
            years=result['expected']['years'],royalty_policy=result['royalty_policy'],calculation=result))
        summary['valuation_note']='Provisional territory fee based on recorded activity and receipts, with 70% total operating expenses including royalties, R25,000 monthly owner salary coverage and R5,000 monthly cash reserve. No growth or additional setup investment assumed; 60-month zero-interest funding. Existing agreement royalty rules apply. Actual operating profit is not recorded. This is not a business or asset sale valuation.'
    return summary
