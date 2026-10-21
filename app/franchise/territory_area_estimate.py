"""Fast, reproducible area-linked planning inputs without an external web crawl."""
import json
from app.franchise.territory_sources import DATA,DEATHS,POPULATION,CENSUS,population_in_geometry,record,stamp


def estimate(geometry,province,research=None):
    reference=json.loads((DATA/'territory_reference.json').read_text(encoding='utf-8'))
    if province not in reference['census2022']:
        raise ValueError('Choose the territory province before updating area-linked figures')
    population,manifest=population_in_geometry(geometry)
    if population is None:raise ValueError('Territory population could not be established')
    household_size=reference['census2022'][province]['household_size']
    current=next((r for r in (research or {}).get('records',[]) if r.get('key')=='mortality_per_1000'
        and r.get('status') in {'available','verified_reference'} and r.get('value') is not None and not (research or {}).get('stale')),None)
    if current:
        rate=float(current['value']);mortality_status=current['status'];mortality_sources=current.get('sources',[DEATHS,POPULATION])
        mortality_period=current.get('data_period','Unknown');checked=current.get('retrieved_at')
    else:
        try:
            from app.franchise.territory_official_reference import mortality_reference
            official=mortality_reference(province)
            rate,mortality_status,mortality_sources=official['rate'],official['status'],official['sources']
            mortality_period='2024';checked=official['retrieved_at']
        except (OSError,ValueError,KeyError):
            deaths=reference['deaths2024'].get(province);total=reference['population2024'].get(province)
            if not deaths or not total:raise ValueError('No supported mortality proxy is available for this province')
            rate=deaths/total*1000;mortality_status='cached_unverified';mortality_sources=[DEATHS,POPULATION]
            mortality_period='2024';checked=stamp()
    values={'population':int(population),'household_size':household_size,'mortality_per_1000':round(rate,6),'demand_basis':'territory'}
    records=[record('population','Modelled territory population',population,'people','medium',
        'Recalculated for the dissolved territory; boundary overlaps are counted once. Not a census count.',
        'WorldPop grid counts weighted by polygon-cell intersection area','Current selected territory',[manifest['source']],
        '2026 projection; WorldPop R2025A v1',status='cached_reference'),
        record('mortality_per_1000','Provincial mortality proxy',round(rate,6),'per 1,000/year','low',
        'Provincial rate applied to a custom territory; imported Compass reference requires online verification' if mortality_status=='cached_unverified' else 'Verified official provincial source; 2024 deaths and same-year population. Archived release retains its verification date; custom territory proxy is not age adjusted',
        'Registered deaths / same-year provincial population × 1,000',province,mortality_sources,mortality_period,status=mortality_status,retrieved_at=checked)]
    return {'inputs':values,'records':records,'estimated_households':round(population/household_size),
        'estimated_annual_deaths':round(population*rate/1000,2),'checked_at':stamp(),'province':province,
        'warnings':['Area-linked figures are planning estimates. Population, mortality and assumed market share affect projected income; recorded franchise income remains unchanged.',
            'Household size uses provincial Census 2022 context. A larger territory does not establish market share, service capacity or profitability.'],
        'mortality_verified':mortality_status in {'available','verified_reference'},'household_source':CENSUS,
        'boundary_coverage':manifest['analysis']}
