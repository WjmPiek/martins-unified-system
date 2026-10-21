from decimal import Decimal
import pytest
from test_native_territory import app, franchise, login, inputs, payload, HEADERS
from app.franchise.territory_finance import profit_margin_fee, calculate, validate_inputs, WEIGHTS


def test_each_annual_margin_is_applied_to_that_years_profit_not_turnover():
    rows=[dict(year=i+1,revenue=1000000,operating=p) for i,p in enumerate([200000,300000,-100000,0,100000])]
    result=profit_margin_fee(rows)
    assert [r['fee_contribution'] for r in result['years']]==[40000,90000,0,0,10000]
    assert result['total']==140000
    assert result['years'][0]['margin_percent']==20
    assert result['years'][2]['margin_percent']==-10
    assert 'not verified as a FASA' in result['provenance']


def test_zero_turnover_does_not_divide_or_square_a_loss_into_a_positive_fee():
    result=profit_margin_fee([dict(year=1,revenue=0,operating=-3000),dict(year=2,revenue=0,operating=1000)])
    assert result['total']==0
    assert all(r['margin_percent'] is None for r in result['years'])


def test_growth_royalties_and_fee_repayments_have_consistent_profit_basis(franchise):
    data=inputs(expense_mode='percentage',expense_percent=70,volume_growth=3,price_growth=4,
        cost_growth=5,territory_fee=150000,fee_method='profit_margin')
    scores={key:3 for key in WEIGHTS};evidence={key:'Reviewed evidence' for key in WEIGHTS}
    result=calculate(franchise,data,scores,evidence)
    fee=result['profit_margin_fee']
    assert fee['selected']
    assert fee['total']==sum(r['fee_contribution'] for r in fee['years'])
    for actual,year in zip(fee['years'],result['expected']['years']):
        assert actual['turnover']==year['revenue'] and actual['profit']==year['operating']
        assert actual['fee_contribution']==(actual['profit']*actual['profit']/actual['turnover']).quantize(Decimal('0.01'))
    assert fee['years'][1]['turnover_growth_percent']>0
    changed=calculate(franchise,{**data,'territory_fee':300000},scores,evidence)
    assert changed['profit_margin_fee']['total']==fee['total']
    assert changed['expected']['years'][0]['repayment']>result['expected']['years'][0]['repayment']
    assert validate_inputs({})['fee_method']=='scorecard'
    with pytest.raises(ValueError,match='Territory Fee method'):
        validate_inputs({'fee_method':'fasa_certified'})


def test_optional_method_survives_save_export_and_print_without_approving_price(app,franchise):
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    data=payload();data['inputs'].update(fee_method='profit_margin',confirmed=False)
    calculated=client.post(base+'/calculate',json=data,headers=HEADERS)
    assert calculated.status_code==200
    assert calculated.json['profit_margin_fee']['selected'] and calculated.json['recommended_fee'] is None
    saved=client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS)
    assert saved.status_code==200
    export=client.get(base+'/export').json
    assert export['payload']['inputs']['fee_method']=='profit_margin'
    assert export['result']['profit_margin_fee']['total']==calculated.json['profit_margin_fee']['total']
    report=client.get(base+'/report?layout=print').get_data(as_text=True)
    assert 'Five-year profit-margin valuation' in report and 'Fee contribution' in report
    assert 'not verified as a FASA-prescribed formula' in report
    assert 'before tax, reserves, financing and territory repayments' in report
    page=client.get(base).get_data(as_text=True)
    assert 'Optional Territory Fee — valuation and affordability' in page


def test_optional_fee_is_reduced_to_monthly_affordability_and_not_the_score_band(franchise):
    data=inputs(fee_method='fair_territory',population=1200000,expense_mode='percentage',expense_percent=70,
        setup=0,working_capital=0,ramp_months=12,owner_salary=25000,target_margin=20,safety_buffer=20)
    result=calculate(franchise,data,{k:3 for k in WEIGHTS},{k:'Reviewed evidence' for k in WEIGHTS})
    fee=result['profit_margin_fee']
    assert 0<fee['fair_fee']==fee['affordable_ceiling']<fee['total']
    assert fee['reduction']==fee['total']-fee['fair_fee']
    assert result['recommended_fee']==fee['fair_fee']
    # The chosen cap passes both scenarios. One Rand more fails a monthly/recovery/buffer check.
    from app.franchise.territory_finance import forecast, affordability_checks, validate_inputs
    def check(amount):
        candidate={**data,'territory_fee':amount}
        return affordability_checks(forecast(franchise,candidate),forecast(franchise,candidate,Decimal('0.8')),validate_inputs(data),owner_budget=True)
    assert all(check(fee['fair_fee']).values())
    assert not all(check(fee['fair_fee']+1).values())


def test_low_population_cannot_support_owner_salary_even_with_positive_paper_profit(franchise):
    data=inputs(fee_method='fair_territory',population=500,expense_mode='percentage',expense_percent=70,
        setup=0,working_capital=0,owner_salary=25000,target_margin=20)
    fee=calculate(franchise,data,{}, {})['profit_margin_fee']
    assert fee['total']>0 and fee['fair_fee']==0 and fee['affordable_ceiling']==0
    assert any('owner salary' in reason for reason in fee['affordability']['reasons'])


def test_population_and_deaths_scale_the_fee_without_land_size_premiums(franchise):
    data=inputs(fee_method='fair_territory',population=6000,expense_mode='percentage',expense_percent=70,
        setup=0,working_capital=0,owner_salary=1000,target_margin=20)
    small=calculate(franchise,data,{}, {})['profit_margin_fee']
    larger=calculate(franchise,{**data,'population':12000}, {}, {})['profit_margin_fee']
    lower_deaths=calculate(franchise,{**data,'mortality_per_1000':5}, {}, {})['profit_margin_fee']
    assert small['fair_fee']>0 and larger['fair_fee']==small['fair_fee']*2
    assert lower_deaths['fair_fee']==small['fair_fee']/2
    assert small['fair_fee']==min(small['total'],small['affordable_ceiling'])
    assert validate_inputs({'fee_method':'profit_margin'})['fee_method']=='fair_territory'


def test_rounding_cents_do_not_fail_the_percentage_expense_target(franchise):
    data=inputs(fee_method='fair_territory',population=6173,burial_price=19999.99,
        expense_mode='percentage',expense_percent=70,setup=0,working_capital=0,
        owner_salary=1000,target_margin=30)
    fee=calculate(franchise,data,{}, {})['profit_margin_fee']
    assert fee['affordability']['checks']['target_margin']
    assert fee['fair_fee']>0
    bad=calculate(franchise,{**data,'expense_percent':71}, {}, {})['profit_margin_fee']
    assert not bad['affordability']['checks']['target_margin'] and bad['fair_fee']==0


def test_invalid_expense_and_royalty_budget_reports_unavailable_instead_of_zero(franchise):
    data=inputs(fee_method='fair_territory',expense_mode='percentage',expense_percent=1)
    fee=calculate(franchise,data,{}, {})['profit_margin_fee']
    assert fee['affordable_ceiling'] is None and fee['fair_fee'] is None
    assert fee['affordability']['status']=='unavailable'


def test_zero_optional_fee_is_explained_in_saved_print_report(app,franchise):
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory'
    data=payload();data['inputs'].update(fee_method='fair_territory',population=500,
        expense_mode='percentage',expense_percent=70,owner_salary=25000,setup=0,working_capital=0)
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    report=client.get(base+'/report?layout=print').get_data(as_text=True)
    assert 'No positive territory fee is supported under these assumptions.' in report
    assert 'Affordable fee ceiling:' in report and 'Reduction for affordability:' in report
