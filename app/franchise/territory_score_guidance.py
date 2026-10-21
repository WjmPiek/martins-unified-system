"""Explainable planning ratings; neutral assumptions never count as verified evidence."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from app.franchise.territory_finance import WEIGHTS

LABELS={"population":"Population and households", "demand":"Annual funeral demand",
    "affordability":"Affordability and service mix", "competition":"Competition", "policy":"Policy sales opportunity",
    "access":"Accessibility", "growth":"Growth potential"}
GUIDANCE={
    "population":("Check population and households inside the combined outline. Expand into adjoining underserved suburbs only where the agreement and service capacity support it.",
        "Provisional size bands: 0 for a verified zero population; 1 below 10,000; 2 from 10,000; 3 from 25,000; 4 from 50,000; 5 from 100,000 residents. Size alone does not establish a viable market."),
    "demand":("Compare estimated resident deaths with this franchise's recorded funerals. Confirm service origins and a realistic market share before expanding capacity.",
        "Provisional activity bands: 0 for verified zero services; 1 below 3; 2 from 3; 3 from 6; 4 from 10; 5 from 20 services per month. Recorded franchise activity is not proof of resident demand in a new territory."),
    "affordability":("Build a locally available cash-package range, including burial and cremation. Compare complete package costs with verified household budgets; confirm transport, cemetery and other inclusions.",
        "1: packages generally exceed verified local budgets; 3: some complete packages fit documented budgets; 5: a broad, sustainable service mix fits verified local budgets. SARS taxable income per assessed taxpayer alone cannot establish household affordability."),
    "competition":("Verify operating funeral providers, burial societies, prices and service gaps inside the outline. Differentiate packages and substantiate the assumed market share.",
        "1: strong verified competitors with little demonstrated differentiation; 3: a competitive market with identifiable service gaps; 5: a verified underserved market with defensible differentiation. Missing directory listings are not evidence of no competition."),
    "policy":("Use this holder's actual policy sales and retained administration income to assess sales capacity. Check conversion, lapses and insurer arrangements before forecasting policy growth.",
        "1: weak documented conversion or retention; 3: established sales capacity with sustainable retention; 5: evidenced, repeatable conversion and retention with a verified addressable market. Household counts alone do not establish eligible customers or future sales."),
    "access":("Check road travel times, collection coverage, mortuary and vehicle capacity, and transport costs from the actual premises to included suburbs. Identify difficult routes and capacity limits.",
        "1: major verified travel or capacity constraints; 3: workable routes and capacity with manageable gaps; 5: tested routes, reliable service capacity and sustainable travel costs. A road map alone does not justify a high score."),
    "growth":("Verify recent population trends, occupied housing developments and local business activity. Use conservative growth assumptions until delivery dates and occupancy are supported by sources.",
        "1: evidenced decline or stalled developments; 3: stable demand with verified moderate growth; 5: strong, sustained growth supported by current local demographic and development evidence. Historical provincial growth is context rather than a local forecast."),
}


def numeric(value):
    try:
        number=Decimal(str(value))
        return number if number.is_finite() and number>=0 else None
    except (InvalidOperation,TypeError,ValueError):return None


def recommendations(payload, research=None, actuals=None):
    records={row['key']:row for row in (research or {}).get('records',[]) if isinstance(row,dict) and 'key' in row}
    checked=datetime.now(timezone.utc).isoformat()
    entries={}
    for key,weight in WEIGHTS.items():
        item=records.get(key)
        context=[]
        if item and item.get('value') is not None:
            context.append(f"{item.get('label',LABELS[key])}: {item['value']} {item.get('unit','')} ({item.get('data_period','date unknown')}; {item.get('confidence','unverified')} confidence).")
        entry=dict(key=key,label=LABELS[key],weight=weight,recommended_score=None,confidence='unverified',
            action=GUIDANCE[key][0],rubric=GUIDANCE[key][1],context=context,sources=(item or {}).get('sources',[]),
            data_period=(item or {}).get('data_period','Unknown'),source_checked_at=(item or {}).get('retrieved_at'),
            reason='Local evidence needs review before recommending a numeric score.',status='review_required')
        usable=bool(item and item.get('confidence') in {'medium','high'} and item.get('status') in {'available','cached_reference'}
            and not (research or {}).get('stale'))
        if key=='population' and usable:
            value=numeric(item.get('value'))
            if value is not None:
                entry.update(recommended_score=0 if value==0 else 1+sum(value>=v for v in [10000,25000,50000,100000]),
                    confidence='low',status='provisional',reason='Population-size planning band; density, household estimates, agreement coverage and local service economics still need review.')
        if key=='demand':
            activity=records.get('capturable_services')
            if payload['inputs'].get('demand_basis')=='franchise_history' and actuals and actuals.get('proposed_inputs'):
                value=numeric(actuals['proposed_inputs'].get('recorded_monthly_services'))
                entry['sources']=[]
                entry['data_period']=actuals['window_start']+' to '+actuals['window_end']
                entry['source_checked_at']=actuals['checked_at']
                entry['context'].append(f"Approved franchise activity: {value} services/month across {actuals['observed_months']} recorded months. Confirm that these services represent the selected territory.")
                usable=value is not None
            else:
                value=numeric((activity or {}).get('value'))
                usable=bool(activity and activity.get('confidence') in {'medium','high'} and activity.get('status') in {'available','cached_reference'} and not (research or {}).get('stale'))
                if activity:
                    entry.update(sources=activity.get('sources',[]),data_period=activity.get('data_period','Unknown'),source_checked_at=activity.get('retrieved_at'))
            if usable and value is not None:
                entry.update(recommended_score=0 if value==0 else 1+sum(value>=v for v in [3,6,10,20]),confidence='low',status='provisional',
                    reason='Activity-based planning band. It remains conditional on service origins, territorial coverage, evidence of market share and financial capacity.')
        if key=='affordability' and item:
            entry['reason']='SARS income is taxpayer context; verified household budgets and comparable local cash packages are still needed.'
        if key=='competition':entry['reason']='Public directory coverage is incomplete. Verify local competitors and service gaps before choosing a score.'
        if key=='access':entry['reason']='Confirm actual journey times, transport costs and service capacity; visible roads alone do not measure access quality.'
        if key=='policy' and actuals and actuals.get('totals'):
            months = actuals.get('observed_months', 0)
            receipts = numeric(actuals['totals'].get('insurance_receipts'))
            payover = numeric(actuals['totals'].get('insurance_payover'))
            if months > 0 and receipts is not None and payover is not None:
                entry.update(data_period=actuals['window_start']+' to '+actuals['window_end'],
                    source_checked_at=actuals['checked_at'], sources=[],
                    reason='Recorded policy cash flow is available. New policy sales, conversion and retention still need evidence before a numeric opportunity score can be justified.')
                entry['context'] = [f"Approved Unified Monthly Figures: {months} recorded months out of {actuals.get('coverage_months', 12)}. "
                    f"Average monthly insurance receipts R{receipts / months:,.2f}; insurer payover R{payover / months:,.2f}; "
                    f"receipts less payover R{(receipts-payover) / months:,.2f}. This difference is before operating expenses and is not a count of policies or new sales.",
                    f"Internal source: franchise {actuals.get('franchise_id', 'selected')}; approved Monthly Figures row IDs: "
                    + ', '.join(str(row['id']) for row in actuals.get('source_rows', []) if 'id' in row)
                    + f". Record confidence: {actuals.get('confidence', 'unverified')}; scope is the franchise's recorded business, not the selected territory's future policy market."]

        if key=='growth':entry['reason']='A local growth score requires recent local evidence; a provincial historical trend is not a territory growth forecast.'
        if (research or {}).get('stale'):
            entry['context'].append('Research is older than 30 days; refresh public findings before accepting a score.')
        if entry['recommended_score'] is None:
            entry.update(recommended_score=3, status='planning_assumption', confidence='unverified',
                reason='Neutral planning assumption: 3/5. This is a starting value, not a researched finding or a verified average. ' + entry['reason'])
        entries[key]=entry
    return dict(version='planning-guidance-v2',generated_at=checked,entries=entries,
        note='Provisional planning guidance. The Compass weights are preserved; these proposed rating bands are not approved Martins standards. Every factor is populated. Where evidence cannot justify a rating, 3/5 is an explicit neutral planning assumption and does not support a recommended territory price. Review every recommendation.')


AUTO_PREFIX = "Automatic suggestion:"
REVIEW_PREFIX = "Automatic evidence review:"


def suggestion_evidence(entry):
    score = entry['recommended_score']
    prefix = f"{AUTO_PREFIX} score={score};" if score is not None else REVIEW_PREFIX
    return (f"{prefix} {entry['confidence']} confidence. {entry['reason']} "
            f"{' '.join(entry['context'])} Data: {entry['data_period']}. "
            f"Source checked: {entry['source_checked_at'] or 'not established'}. "
            f"Sources: {', '.join(entry['sources']) or 'No verified source for a numeric score'}. "
            f"Next step: {entry['action']} {entry['rubric']}")


def populate_suggestions(payload, guidance):
    """Fill supported numbers and evidence; preserve manually reviewed values."""
    import re
    for key, entry in guidance['entries'].items():
        previous = payload['score_evidence'].get(key, '').strip()
        match = re.match(r'Automatic suggestion: score=([0-5](?:\.\d+)?);', previous)
        automatic = bool(match and numeric(payload['scores'].get(key)) == numeric(match.group(1)))
        empty = key not in payload['scores']
        if empty or automatic:
            if entry['recommended_score'] is not None:
                payload['scores'][key] = entry['recommended_score']
            else:
                payload['scores'].pop(key, None)
            if not previous or automatic or previous.startswith(REVIEW_PREFIX):
                payload['score_evidence'][key] = suggestion_evidence(entry)
        elif not previous:
            payload['score_evidence'][key] = suggestion_evidence(entry)
