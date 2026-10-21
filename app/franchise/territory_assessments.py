"""Independent presentation models preserved in each valuation snapshot."""
from copy import deepcopy
from urllib.parse import urlsplit
from app.franchise.territory_finance import calculate, COMMERCIAL_FEES
from app.franchise.territory_prospects import suggested_inputs, assumption_ledger
from app.franchise.territory_score_guidance import recommendations, populate_suggestions

LABELS = {'commercial':'Martins commercial territory fee',
          'running':'Running concern — recorded business',
          'prospective':'New prospective franchise — suggested territory fee'}


def safe_link(value):
    try:
        url=urlsplit(value or '')
        return value if url.scheme=='https' and url.hostname and not url.username and not url.password else '#'
    except ValueError:
        return '#'


def build_assessments(franchise, payload, result):
    area=result.get('area_estimate')
    research=result.get('research')
    public=deepcopy(payload)
    if payload.get('scenario_mode')!='prospective':
        public.update(inputs={**suggested_inputs(),**(area or {}).get('inputs',{}),
            'commercial_category':payload['inputs'].get('commercial_category','standard')},
            scores={},score_evidence={},sources=[],scenario_mode='prospective',
            prospect={'name':'Selected area prospective franchise','royalty_percent':6},
            financial_baseline=None,comparison_baseline=None)
    # Comparisons must never be interpreted as the prospective model's source.
    public['financial_baseline']=None
    public['comparison_baseline']=None
    public.pop('running_assumptions',None)
    public['inputs']['demand_basis']='territory'
    public['inputs']['recorded_monthly_services']=0
    guidance_research={**(research or {}),'records':[
        *(research or {}).get('records',[]),*(area or {}).get('records',[])]}
    guide=recommendations(public,guidance_research,None)
    populate_suggestions(public,guide)
    entries={}

    def presentation(key, data, native, actuals=None):
        native=deepcopy(native)
        for scenario in ('expected','downside'):native[scenario].pop('months',None)
        native.update(research=deepcopy(research),area_estimate=deepcopy(area),
            selected_service_prices=deepcopy(result.get('selected_service_prices',{})),
            score_recommendations=recommendations(data,guidance_research,actuals))
        if key=='running':
            native['financial_baseline']=deepcopy({k:v for k,v in actuals.items() if k!='valuation'})
            native['running_concern']=deepcopy(result['running_concern'])
            native['running_concern']['valuation'].pop('calculation',None)
            native['selected_service_prices']={}
        elif data.get('scenario_mode')=='prospective':
            native['prospective']=assumption_ledger(data,area,research)
        elif actuals:
            native['financial_baseline']=deepcopy({k:v for k,v in actuals.items() if k!='valuation'})
        native['recommended_fee']=None
        native['recommendation_reason']='Provisional scenario calculation. Verify all local evidence, assumed costs and agreement terms before approving a territory price.'
        return dict(key=key,label=LABELS[key],available=True,payload=data,result=native,
            fee=data['inputs']['territory_fee'],basis='Recorded business activity and explicit cost assumptions.' if key=='running' else 'Entered forecast with Martins commercial fee.' if key=='commercial' else 'Selected area research and prospective planning assumptions.')

    initial=calculate(None,public['inputs'],public['scores'],public['score_evidence'],
        planning_royalty=public['prospect']['royalty_percent'])
    for key in ('commercial','prospective'):
        data=deepcopy(payload if key=='commercial' else public)
        data.pop('running_assumptions',None)
        fee=COMMERCIAL_FEES[data['inputs']['commercial_category']] if key=='commercial' else initial['profit_margin_fee']['fair_fee']
        if fee is None:
            entries[key]=dict(key=key,label=LABELS[key],available=False,fee=None,
                reason='Correct the planning assumptions before calculating this fee.')
            continue
        data['inputs'].update(territory_fee=float(fee),fee_method='commercial' if key=='commercial' else 'fair_territory')
        is_prospect=data.get('scenario_mode')=='prospective'
        native=calculate(None if is_prospect else franchise,data['inputs'],data['scores'],data['score_evidence'],
            planning_royalty=data['prospect']['royalty_percent'] if is_prospect else None)
        entries[key]=presentation(key,data,native,result.get('financial_baseline') if key=='commercial' else None)
        if key=='commercial':entries[key]['result']['recommendation_reason']=result['recommendation_reason']
    running=result.get('running_concern')
    valuation=(running or {}).get('valuation')
    if valuation and valuation.get('calculation'):
        data=deepcopy(payload)
        data.update(inputs={**deepcopy(valuation['inputs']),**{k:v for k,v in (area or {}).get('inputs',{}).items() if k in {'population','mortality_per_1000','household_size'}}},scenario_mode='existing',scores={},score_evidence={})
        guide=recommendations(data,guidance_research,running)
        populate_suggestions(data,guide)
        native=calculate(franchise,data['inputs'],data['scores'],data['score_evidence'])
        entries['running']=presentation('running',data,native,running)
    else:
        entries['running']=dict(key='running',label=LABELS['running'],available=False,fee=None,
            reason='Load at least three approved months with positive funeral activity using the running-concern card. No recorded figures or fee have been invented.')
    return {key:entries[key] for key in LABELS}
