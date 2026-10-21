from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import pytest
from test_native_territory import app, franchise, login, payload, HEADERS
from app.franchise.territory_fields import LABELS, field_spec, field_value
from app.franchise.territory_finance import DEFAULTS
from app.franchise import territory_boundaries as boundaries
from app.franchise.territory_boundary_cache import cached_boundary_json


def randburg():
    return {'features':[{'properties':{'MP_CODE':798016,'MP_NAME':'Randburg','MN_NAME':'City of Johannesburg','MN_CODE':798,'PR_NAME':'Gauteng'},
        'geometry':{'type':'Polygon','coordinates':[[[27.9,-26.1],[27.91,-26.1],[27.91,-26.09],[27.9,-26.09],[27.9,-26.1]]]}}],
        '_source_meta':{'retrieved_at':'2026-10-21T08:00:00+00:00'}}


def test_automatic_randburg_search_uses_real_town_type_and_johannesburg_code(monkeypatch):
    monkeypatch.setattr(boundaries,'public_json',lambda url,params:randburg())
    result=boundaries.query_boundaries(query='Randburg',kind='auto')
    area=result['areas'][0]
    assert result['matched_kind']=='town' and area['kind']=='town'
    assert area['municipality_code']=='JHB' and area['census_municipality_code']=='798'
    assert area['data_date']=='2011-10-09' and area['source']==boundaries.MAIN
    assert area['retrieved_at']=='2026-10-21T08:00:00+00:00'
    assert '2011' in result['message']


def test_automatic_search_can_recover_when_one_publisher_is_unavailable(monkeypatch):
    def publisher(url,params):
        if url.startswith(boundaries.MAIN):raise ConnectionError('Publisher down')
        if '/items/' in url:
            return dict(owner='MDBUser1',type='Feature Service',title='Official municipal boundaries',
                url=boundaries.MDB_BASE+boundaries.MDB_RELEASES[(2021,'municipality')][1]+'/FeatureServer')
        return {'features':[{'properties':{'CAT_B':'JHB','MUNICNAME':'City of Johannesburg','PROVINCE':'GT'},'geometry':randburg()['features'][0]['geometry']}]}
    monkeypatch.setattr(boundaries,'public_json',publisher)
    result=boundaries.query_boundaries(query='JHB',kind='auto')
    assert result['matched_kind']=='municipality'
    assert result['areas'][0]['municipality_code']=='JHB'
    assert 'Another boundary source was unavailable' in result['message']
    with pytest.raises(ValueError):boundaries.query_boundaries(query='',kind='auto')


def test_boundary_cache_preserves_source_date_and_never_substitutes_another_query(tmp_path):
    url='https://publisher.example/query';params={'where':'Randburg'}
    first=cached_boundary_json(lambda *_:randburg(),url,params,tmp_path)
    def offline(*_):raise ConnectionError('offline')
    stored=cached_boundary_json(offline,url,params,tmp_path)
    assert stored['features']==first['features'] and stored['_boundary_cache']
    assert stored['_source_meta']['retrieved_at']==first['_source_meta']['retrieved_at']
    with pytest.raises(RuntimeError):cached_boundary_json(offline,url,{'where':'Another area'},tmp_path)
    assert cached_boundary_json(lambda *_:{'error':{'code':503}},url,params,tmp_path)['features']==first['features']


@pytest.mark.parametrize('problem',['expired','corrupted'])
def test_boundary_cache_rejects_stale_or_altered_copies(tmp_path,problem):
    cached_boundary_json(lambda *_:randburg(),'https://publisher.example/query',{},tmp_path)
    path=next(tmp_path.glob('*.json'));saved=json.loads(path.read_text(encoding='utf-8'))
    if problem=='expired':saved['stored_at']=(datetime.now(timezone.utc)-timedelta(days=31)).isoformat()
    else:saved['body']['features'][0]['properties']['MP_NAME']='Wrong area'
    path.write_text(json.dumps(saved),encoding='utf-8')
    def offline(*_):raise ConnectionError('offline')
    with pytest.raises(RuntimeError):cached_boundary_json(offline,'https://publisher.example/query',{},tmp_path)


def test_every_field_has_a_label_unit_and_report_format():
    assert set(DEFAULTS)<=set(LABELS)
    assert field_value('owner_salary','25000')=='R 25,000.00'
    assert field_value('market_share',10)=='10.00%'
    assert field_value('population',17648)=='17,648'
    assert field_value('start_month','1.0')=='January'
    assert field_value('start_year',2027)=='2027'
    assert field_value('vat_recoverable',True)=='Yes'
    assert field_spec('vat_percent')['maximum']==30
    assert field_spec('mortality_per_1000')['step']=='0.000001'


def test_editor_removes_checkboxes_and_manual_salary_is_added_once(app,franchise):
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    page=client.get(base).get_data(as_text=True)
    assert 'id="input-salary_in_fixed"' not in page and 'id="show-individual-boundaries"' not in page
    assert '<option value="auto">Town, suburb or municipality</option>' in page
    assert 'Owner salary per month (R)' in page and 'Expected market share (%)' in page
    assert 'step="0.01"' in page and 'January' in page
    data=payload();data['inputs'].update(expense_mode='manual',fixed_cost=60000,owner_salary=25000,salary_in_fixed=False)
    calculated=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert calculated.status_code==200
    assert Decimal(calculated.json['expected']['months'][0]['overhead'])==85000
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    exported=client.get(base+'/export').json
    assert exported['payload']['inputs']['salary_in_fixed'] is False
    report=client.get(base+'/report').get_data(as_text=True)
    assert 'R 25,000.00' in report and '10.00%' in report and 'January' in report
    data['inputs']['expense_mode']='percentage'
    row=client.post(base+'/calculate',json=data,headers=HEADERS).json['expected']['months'][0]
    assert Decimal(row['overhead'])+Decimal(row['royalty'])==Decimal(row['revenue'])*Decimal('0.70')


def test_all_site_templates_compile_after_copy_review(app):
    for name in app.jinja_env.list_templates():app.jinja_env.get_template(name)


def test_legacy_salary_split_preserves_combined_cost_and_is_idempotent():
    from app.franchise.territory_fields import separate_owner_salary,field_label
    raw=dict(fixed_cost=60000,owner_salary=25000,salary_in_fixed=True,expense_mode='manual')
    split=separate_owner_salary(raw)
    assert Decimal(split['fixed_cost'])==35000 and split['owner_salary']==25000
    assert split['salary_in_fixed'] is False and raw['salary_in_fixed'] is True
    assert separate_owner_salary(split)==split
    assert 'including owner salary' in field_label('fixed_cost',raw)
    assert 'excluding owner salary' in field_label('fixed_cost',split)


def test_readable_dates_preserve_periods_and_convert_utc_to_sast():
    from app.franchise.territory_fields import display_date
    assert display_date('2026-10-21T23:15:00+00:00')=='22 October 2026, 01:15 SAST'
    assert display_date('2011-10-09')=='09 October 2011'
    assert display_date('2027-01')=='January 2027'
    assert display_date('2026 projection; WorldPop R2025A v1')=='2026 projection; WorldPop R2025A v1'
    assert display_date('not stated')=='not stated'
