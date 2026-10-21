"""Verified official release archive; never relabel the Compass fallback."""
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]/'data'/'territory_official_sources'


@lru_cache(maxsize=4)
def load_reference(root=None):
    root=Path(root or ROOT)
    data=json.loads((root/'mortality-2024.json').read_text(encoding='utf-8'))
    if data.get('schema_version')!=1 or data.get('death_year')!=2024 or data.get('population_year')!=2024:
        raise ValueError('Official reference years or schema are invalid')
    checked=datetime.fromisoformat(data['verified_at'])
    if checked.tzinfo is None or checked>datetime.now(timezone.utc):
        raise ValueError('Invalid official reference verification date')
    for source in data['documents']:
        if source['file'] not in {'P030932024.pdf','P03022024.pdf'}:
            raise ValueError('Unexpected official reference document')
        expected='https://www.statssa.gov.za/publications/'+('P03093/' if source['file']=='P030932024.pdf' else 'P0302/')+source['file']
        if source['url']!=expected or hashlib.sha256((root/source['file']).read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('Official reference document integrity check failed')
    if len(data['documents'])!=2 or len({s['file'] for s in data['documents']})!=2:
        raise ValueError('Both same-year official sources are required')
    if len(data['provinces'])!=9:
        raise ValueError('Official province coverage is incomplete')
    for row in data['provinces'].values():
        if any(type(row[k]) is not int or row[k]<=0 for k in ['resident_deaths','population']):
            raise ValueError('Invalid official province counts')
        if not 0 < row['resident_deaths']/row['population']*1000 < 50:
            raise ValueError('Official mortality rate failed range checks')
    return data


def mortality_reference(province, root=None):
    data=load_reference(str(root or ROOT))
    row=data['provinces'].get(province)
    if not row:raise ValueError('Province is missing from the official reference')
    return {'rate':row['resident_deaths']/row['population']*1000,
        'retrieved_at':data['verified_at'],'status':'verified_reference',
        'sources':[s['url'] for s in data['documents']],
        'reason':'Verified Stats SA 2024 usual-residence deaths and same-year population from archived official publications. Provincial proxy; not age adjusted or a local death count.',
        'provenance':{'death_year':2024,'population_year':2024,'population_series':2024,
            'resident_deaths':row['resident_deaths'],'denominator':row['population'],
            'source_verified_at':data['verified_at'],'documents':data['documents']}}
