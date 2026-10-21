from copy import deepcopy
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import pytest
from test_native_territory import app,franchise,login,payload,HEADERS
from test_territory_research import polygon
from test_territory_actuals import seed,grant_monthly
from app.extensions import db
from app.models import Role,RoyaltyScale
from app.franchise import territory_area_estimate as estimates,territory_finance as finance


def area(population=600000):
    return dict(inputs=dict(population=population,mortality_per_1000=10,household_size=3,demand_basis='territory'),
        records=[dict(key='population',label='Modelled territory population',value=population,unit='people',confidence='medium',
            status='cached_reference',sources=['https://example.com/population'],method='Full selected polygon grid intersection',
            data_period='2026 population model',retrieved_at='2026-10-20T10:00:00+00:00')],
        estimated_households=population//3,estimated_annual_deaths=population/100,mortality_verified=True,
        household_source='https://example.com/census',warnings=[],province='Gauteng')


def starter():
    return dict(geometry=polygon(),boundary_note='Sourced selected planning area',area_province='Gauteng',
        name='Randburg prospective franchise',inputs={'burial_price':999999,'policy_receipts':777777},financial_baseline='a'*64)


def fail_holder(*args,**kwargs):raise AssertionError('Prospective scenario read a holder financial record or contract')


def test_prospective_reset_never_reads_holder_finances_or_royalty_contract(app,franchise,monkeypatch):
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    monkeypatch.setattr(estimates,'estimate',lambda *a:area())
    from app.franchise import territory_actuals
    monkeypatch.setattr(territory_actuals,'baseline',fail_holder)
    for name in ['get_royalty_scales','select_royalty_method','calculate_royalty_amount']:monkeypatch.setattr(finance,name,fail_holder)
    response=client.post(base+'/prospect-suggestions',json=starter(),headers=HEADERS)
    assert response.status_code==200,response.json
    data=response.json['payload'];result=response.json['result']
    assert data['scenario_mode']=='prospective' and data['financial_baseline'] is None and data['comparison_baseline'] is None
    assert data['inputs']['policy_receipts']==0 and data['inputs']['recorded_monthly_services']==0
    assert data['inputs']['burial_price']==20000 and data['inputs']['expense_percent']==70
    assert data['inputs']['population']==600000 and result['commercial_fee']['amount']=='150000'
    assert result['profit_margin_fee']['fair_fee'] is not None and 'running_concern' not in result
    assert result['royalty_policy']['is_planning'] and result['royalty_policy']['scale_source_id'] is None
    assert Decimal(result['expected']['months'][0]['royalty'])==(Decimal(result['expected']['months'][0]['revenue'])*Decimal('.06')).quantize(Decimal('.01'))
    assert all(row['basis'] and row['confidence'] for row in result['prospective']['rows'])
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    report=client.get(base+'/report?layout=print').get_data(as_text=True)
    assert 'Randburg prospective franchise' in report and 'Prospective area assumptions and sources' in report
    assert 'No existing-holder contract' in report and 'Running concern' in report
    assert client.get(base+'/export').json['payload']['scenario_mode']=='prospective'


def test_running_comparison_cannot_change_prospective_forecast_fees_or_scores(app,franchise,monkeypatch):
    client=login(app,franchise);grant_monthly();base=f'/franchise/{franchise.id}/territory'
    monkeypatch.setattr(estimates,'estimate',lambda *a:area())
    seed(franchise.id,datetime.now(timezone(timedelta(hours=2))).date())
    response=client.post(base+'/prospect-suggestions',json=starter(),headers=HEADERS)
    assert response.status_code==200,response.json
    data=response.json['payload'];before=response.json['result']
    actuals=client.post(base+'/franchise-figures',json={},headers=HEADERS).json
    data['comparison_baseline']=actuals['fingerprint']
    compared=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert compared.status_code==200,compared.json
    after=compared.json
    for key in ['expected','downside','commercial_fee','profit_margin_fee','calculated_scores']:
        assert before[key]==after[key],key
    assert after['running_concern']['comparison_only']
    running=after['running_concern']['valuation']
    assert running is not None
    assert running['inputs']['demand_basis']=='franchise_history'
    assert running['inputs']['recorded_monthly_services']==5
    assert running['inputs']['setup']==0 and Decimal(running['inputs']['expense_percent'])==70
    assert len(running['years'])==5 and 'fair_fee' in running['fee']
    assert 'Actual operating profit is not recorded' in after['running_concern']['valuation_note']
    assert Decimal(after['running_concern']['monthly_business_income'])==15200
    assert Decimal(after['running_concern']['monthly_services'])==5
    assert data['inputs']['policy_receipts']==0 and data['financial_baseline'] is None
    # Even a changed holder agreement must not affect the area-only plan.
    RoyaltyScale.query.filter_by(franchise_id=franchise.id).first().percentage=33
    db.session.commit()
    changed=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert changed.status_code==200 and changed.json['expected']==before['expected']
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    exported=client.get(base+'/export').json
    assert Decimal(exported['result']['running_concern']['monthly_business_income'])==15200
    # Comparison records retain the existing Monthly Figures permission gate.
    role=Role.query.filter_by(name='Admin').one();role.permissions=[p for p in role.permissions if p.code!='monthly_figures:view'];db.session.commit()
    assert client.get(base+'/report').status_code==403 and client.get(base+'/export').status_code==403
    assert client.get(base+'?scenario=prospective').status_code==200


@pytest.mark.parametrize('change',[{'financial_baseline':'a'*64},{'scenario_mode':{}},{'prospect':{'name':'Area','royalty_percent':101}}])
def test_prospective_rejects_holder_baseline_and_invalid_mode_or_royalty(app,franchise,monkeypatch,change):
    client=login(app,franchise);monkeypatch.setattr(estimates,'estimate',lambda *a:area());base=f'/franchise/{franchise.id}/territory'
    response=client.post(base+'/prospect-suggestions',json=starter(),headers=HEADERS)
    data=response.json['payload'];data.update(change)
    assert client.post(base+'/calculate',json=data,headers=HEADERS).status_code==400
