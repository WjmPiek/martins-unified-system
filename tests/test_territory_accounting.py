from decimal import Decimal
from copy import deepcopy
import pytest
from test_native_territory import app, franchise, inputs, login, payload, HEADERS
from app.franchise.territory_finance import forecast, calculate, profit_margin_fee
from app.franchise.territory_prospects import running_assumptions


def accounting_inputs(**changes):
    return inputs(burial_price=10000, expense_mode='percentage', expense_percent=70,
        annual_depreciation=12000, annual_amortisation=6000, annual_interest=6000,
        tax_percent=25, annual_capex=12000, annual_working=6000, annual_finance=3600,
        reserve=2000, territory_fee=120000, **changes)


def test_hand_calculated_profit_cash_and_principal_reconcile(franchise):
    row=forecast(franchise,accounting_inputs())['months'][0]
    assert row['revenue']==100000
    assert row['royalty']+row['overhead']+row['direct']==70000
    assert row['ebitda']==30000 and row['operating']==28500
    assert row['profit_before_tax']==28000 and row['tax']==7000
    assert row['net_profit']==21000 and row['operating_cash']==22500
    assert row['reserve']==2000  # Tax and capex must not consume the reserve allocation.
    assert row['cash_available']==18700
    assert row['principal_repayment']==2000 and row['retained']==16700


def test_territory_interest_is_deducted_once_and_principal_is_not_an_expense(franchise):
    data=accounting_inputs(interest_percent=12)
    row=forecast(franchise,data)['months'][0]
    assert row['interest']==1200 and row['total_interest']==1700
    assert row['net_profit']==20100
    assert row['repayment']==row['principal_repayment']+row['interest']
    assert row['retained']==row['net_profit']+row['noncash']-row['cash_deductions']-row['principal_repayment']
    full=forecast(franchise,data)
    assert sum(r['principal_repayment'] for r in full['months'])==120000


def test_right_amortisation_changes_profit_not_principal_or_cash_twice(franchise):
    base=forecast(franchise,accounting_inputs())
    amortised=forecast(franchise,accounting_inputs(territory_amortisation_months=60))
    row=amortised['months'][0]
    assert row['territory_amortisation']==2000
    assert row['net_profit']==19500
    assert row['principal_repayment']==base['months'][0]['principal_repayment']
    assert row['retained']==base['months'][0]['retained']+500  # Planning tax provision falls; non-cash amortisation is added back.
    assert sum(r['territory_amortisation'] for r in amortised['months'])==120000


def test_fee_uses_after_tax_profit_before_new_territory_funding(franchise):
    data=accounting_inputs(interest_percent=12, fee_method='fair_territory')
    result=calculate(franchise,data,{}, {})
    fee=result['profit_margin_fee']
    assert fee['years'][0]['profit']==252000
    assert fee['years'][0]['margin_percent']==21
    assert fee['years'][0]['fee_contribution']==52920
    assert fee['total']==264600
    changed=calculate(franchise,{**data,'territory_fee':800000}, {}, {})
    assert changed['profit_margin_fee']['total']==fee['total']
    assert changed['expected']['years'][0]['net_profit']<result['expected']['years'][0]['net_profit']


def test_discount_is_an_explicit_comparison_not_a_second_fee(franchise):
    data=accounting_inputs(fee_method='fair_territory')
    plain=calculate(franchise,data,{}, {})
    discounted=calculate(franchise,{**data,'discount_percent':10}, {}, {})
    assert plain['profit_margin_fee']['discounted_total'] is None
    assert 0<discounted['profit_margin_fee']['discounted_total']<plain['profit_margin_fee']['total']
    assert discounted['profit_margin_fee']['fair_fee']==plain['profit_margin_fee']['fair_fee']
    assert discounted['expected']==plain['expected']


def test_losses_cannot_create_fee_and_unknown_tax_is_visible(franchise):
    fee=profit_margin_fee([dict(year=1,revenue=1000000,net_profit=-200000),dict(year=2,revenue=0,net_profit=100)])
    assert fee['total']==0
    result=calculate(franchise,inputs(tax_percent=0),{}, {})
    assert any('0% tax' in note for note in result['accounting_notes'])


def test_running_assumptions_are_restricted_and_default_to_no_growth():
    assert running_assumptions()['tax_percent']==27
    assert running_assumptions()['volume_growth']==0
    assert running_assumptions({'volume_growth':3})['volume_growth']==3
    with pytest.raises(ValueError): running_assumptions({'burial_price':999999})
    with pytest.raises(ValueError): running_assumptions({'tax_percent':101})


def test_new_accounting_results_and_report_reconcile_without_operational_writes(app,franchise):
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    data=payload();data['inputs']=accounting_inputs(fee_method='fair_territory')
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    saved=client.get(base+'/export').json
    assert saved['result']['accounting_version']=='martins-accounting-v2'
    report=client.get(base+'/report?layout=print').get_data(as_text=True)
    for text in ['Net profit after tax','Profit before tax','Territory principal repayments','Cash reserve allocation','Discounted contribution comparison']:
        assert text in report
    assert client.get(base+'/export').json==saved


def test_running_growth_and_tax_edits_cannot_change_the_prospective_model(app,franchise,monkeypatch):
    from datetime import datetime, timezone, timedelta
    from test_territory_actuals import seed, grant_monthly
    from test_territory_prospects import area, starter
    from app.franchise import territory_area_estimate
    client=login(app,franchise);grant_monthly();base=f'/franchise/{franchise.id}/territory'
    monkeypatch.setattr(territory_area_estimate,'estimate',lambda *args:area())
    seed(franchise.id,datetime.now(timezone(timedelta(hours=2))).date())
    data=client.post(base+'/prospect-suggestions',json=starter(),headers=HEADERS).json['payload']
    data['comparison_baseline']=client.post(base+'/franchise-figures',json={},headers=HEADERS).json['fingerprint']
    original=client.post(base+'/calculate',json=data,headers=HEADERS).json
    data['running_assumptions']={'volume_growth':3,'tax_percent':20,'annual_depreciation':12000}
    changed=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert changed.status_code==200
    changed=changed.json
    for key in ('prospective','commercial'):
        before,after=original['assessments'][key],changed['assessments'][key]
        assert before['fee']==after['fee']
        assert before['payload']['inputs']==after['payload']['inputs']
        assert 'running_assumptions' not in after['payload']
        for metric in ('expected','downside','profit_margin_fee','scorecard'):
            assert before['result'][metric]==after['result'][metric]
    running=changed['running_concern']['valuation']
    assert Decimal(running['inputs']['tax_percent'])==20
    assert Decimal(running['years'][1]['revenue'])>Decimal(running['years'][0]['revenue'])
    assert Decimal(running['years'][0]['net_profit'])<Decimal(original['running_concern']['valuation']['years'][0]['net_profit'])


def test_legacy_snapshot_is_not_repriced_when_viewed_or_when_a_new_draft_is_calculated(app,franchise):
    import json
    from app.extensions import db
    from app.models import TerritoryValuationSnapshot
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    data=payload()
    client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS)
    snapshot=TerritoryValuationSnapshot.query.one()
    stored=json.loads(snapshot.result_json)
    for result in [stored,*[a['result'] for a in stored['assessments'].values() if a.get('available')]]:
        result.pop('accounting_version',None);result.pop('accounting_notes',None)
        result['profit_margin_fee'].pop('profit_measure',None)
        result['profit_margin_fee']['formula']='Legacy operating-profit pricing basis preserved.'
    snapshot.result_json=json.dumps(stored);db.session.commit()
    before=snapshot.result_json
    report=client.get(base+'/report?layout=print').get_data(as_text=True)
    assert 'Legacy operating-profit pricing basis preserved.' in report
    assert 'Cash available before territory principal' not in report
    assert client.post(base+'/calculate',json=data,headers=HEADERS).json['accounting_version']=='martins-accounting-v2'
    db.session.refresh(snapshot)
    assert snapshot.result_json==before


@pytest.mark.parametrize('confirmed', [False, True])
def test_legacy_zero_tax_example_upgrade_preserves_confirmed_rates_and_saved_data(app,franchise,confirmed):
    import json
    from app.extensions import db
    from app.models import TerritoryValuationSnapshot
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    data=payload();data.update(scenario_mode='prospective',prospect={'name':'Legacy example','royalty_percent':6},area_province='Gauteng')
    data['inputs'].update(tax_percent=0,confirmed=confirmed)
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    snapshot=TerritoryValuationSnapshot.query.one()
    legacy=json.loads(snapshot.result_json);legacy.pop('accounting_version')
    snapshot.result_json=json.dumps(legacy);db.session.commit()
    page=client.get(base).get_data(as_text=True)
    context=json.loads(page.split('<script id="territory-context" type="application/json">')[1].split('</script>')[0])
    assert context['payload']['inputs']['tax_percent']==(0 if confirmed else 27)
    assert not context['payload']['inputs']['confirmed']
    assert client.get(base+'/export').json['payload']['inputs']['tax_percent']==0
