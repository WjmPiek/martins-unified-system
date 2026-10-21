from datetime import datetime,timezone,timedelta
from decimal import Decimal
from test_native_territory import app,franchise,login,HEADERS
from test_territory_prospects import area,starter
from test_territory_actuals import seed,grant_monthly
from app.franchise import territory_area_estimate as estimates,territory_actuals


def test_assessments_preserve_independent_inputs_repayments_and_sources(app,franchise,monkeypatch):
    client=login(app,franchise);grant_monthly();base=f'/franchise/{franchise.id}/territory'
    monkeypatch.setattr(estimates,'estimate',lambda *a:area())
    seed(franchise.id,datetime.now(timezone(timedelta(hours=2))).date())
    response=client.post(base+'/prospect-suggestions',json=starter(),headers=HEADERS)
    data=response.json['payload']
    data['comparison_baseline']=client.post(base+'/franchise-figures',json={},headers=HEADERS).json['fingerprint']
    calculated=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert calculated.status_code==200,calculated.json
    result=calculated.json;entries=result['assessments']
    assert set(entries)=={'commercial','running','prospective'}
    assert all(entry['available'] for entry in entries.values())
    assert Decimal(str(entries['commercial']['fee']))==150000
    assert entries['running']['payload']['inputs']['recorded_monthly_services']==5
    assert entries['running']['payload']['inputs']['demand_basis']=='franchise_history'
    assert entries['running']['payload']['inputs']['population']==600000
    assert Decimal(entries['running']['result']['expected']['years'][0]['services'])==60
    assert entries['prospective']['payload']['inputs']['recorded_monthly_services']==0
    assert entries['prospective']['payload']['inputs']['policy_receipts']==0
    assert entries['prospective']['result']['royalty_policy']['is_planning']
    assert not entries['running']['result']['royalty_policy'].get('is_planning')
    for entry in entries.values():
        inputs=entry['payload']['inputs'];native=entry['result']
        assert Decimal(str(inputs['territory_fee']))==Decimal(str(entry['fee']))
        total=sum(Decimal(y['repayment']) for y in native['expected']['years'])
        assert total==Decimal(str(entry['fee'])).quantize(Decimal('.01'))
        assert len(native['score_recommendations']['entries'])==7
    html=result['assessment_html']
    for title in ['Five-year financial projection','Demand and service mix','Operating costs and income',
            'Territory purchase and funding','Projection and affordability','Applied franchise agreement',
            'Where the information was found','Recommendations for all commercial scorecard factors']:
        assert html.count('<h2>'+title+'</h2>')==3,title
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    # A saved report must render the saved scenarios without re-reading current records.
    monkeypatch.setattr(territory_actuals,'baseline',lambda *a:(_ for _ in ()).throw(AssertionError('Snapshot reread records')))
    report=client.get(base+'/report?version=1&layout=print&assessment=running')
    assert report.status_code==200
    text=report.get_data(as_text=True)
    assert 'Assessment to print' in text and 'data-select-assessment="running"' in text
    assert 'data-assessment-panel="prospective"' in text
    exported=client.get(base+'/export').json
    for key in entries:
        saved=exported['result']['assessments'][key]
        assert saved['fee']==entries[key]['fee']
        assert saved['payload']['inputs']==entries[key]['payload']['inputs']
        assert saved['result']['expected']==entries[key]['result']['expected']
    assert client.get(base+'/export').json==exported


def test_running_card_is_unavailable_without_approved_data(app,franchise,monkeypatch):
    client=login(app,franchise);monkeypatch.setattr(estimates,'estimate',lambda *a:area())
    result=client.post(f'/franchise/{franchise.id}/territory/prospect-suggestions',json=starter(),headers=HEADERS).json['result']
    assert result['assessments']['running']['available'] is False
    assert result['assessments']['running']['fee'] is None
    assert 'data-available="false"' in result['assessment_html']
