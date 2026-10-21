from decimal import Decimal
import pytest
from test_native_territory import app,franchise,login,inputs,payload,HEADERS
from app.franchise import territory_finance as finance


@pytest.mark.parametrize('category,amount',[('small',75000),('standard',150000),('large',200000)])
def test_commercial_fees_are_separate_from_fairness_and_never_added_together(franchise,category,amount):
    data=inputs(commercial_category=category,fee_method='commercial',territory_fee=amount,expense_mode='percentage',expense_percent=70)
    result=finance.calculate(franchise,data,{},{});commercial=result['commercial_fee'];fair=result['profit_margin_fee']
    assert commercial['amount']==amount and commercial['rates']=={'small':75000,'standard':150000,'large':200000}
    assert commercial['difference']==abs(fair['fair_fee']-amount)
    assert commercial['selected'] and not fair['selected']
    assert sum(row['repayment'] for row in result['expected']['months'])==amount
    assert fair['fair_fee']!=amount+fair['total']


def test_both_cards_and_category_survive_save_export_and_print(app,franchise):
    client=login(app,franchise);base=f'/franchise/{franchise.id}/territory';data=payload()
    data['inputs'].update(commercial_category='large',territory_fee=200000,fee_method='commercial')
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    exported=client.get(base+'/export').json
    assert exported['result']['commercial_fee']['amount']=='200000' and exported['payload']['inputs']['commercial_category']=='large'
    for suffix in ['', '/report?layout=print']:
        html=client.get(base+suffix).get_data(as_text=True)
        assert 'Martins commercial territory fee' in html and 'fairness comparison' in html
        assert 'R 75,000.00' in html and 'R 150,000.00' in html and 'R 200,000.00' in html
        if suffix:assert 'Selected purchase-fee method: Martins commercial fee.' in html
    data['inputs']['territory_fee']=123
    assert client.post(base+'/calculate',json=data,headers=HEADERS).status_code==400


def test_fee_search_reuses_operating_figures_without_changing_amortisation_or_profit(franchise,monkeypatch):
    calls=[];original=finance.select_royalty_method
    def counted(*args,**kwargs):calls.append(kwargs);return original(*args,**kwargs)
    monkeypatch.setattr(finance,'select_royalty_method',counted)
    data=inputs(expense_mode='percentage',expense_percent=70,interest_percent=12,repayment_months=36,vat_recoverable=False)
    cache={};first=finance.forecast(franchise,data,royalty_cache=cache)
    assert len(calls)==60
    changed={**data,'territory_fee':300000}
    reused=finance.forecast(franchise,changed,royalty_cache=cache)
    assert len(calls)==60
    fresh=finance.forecast(franchise,changed)
    assert len(calls)==120 and finance.serializable(reused)==finance.serializable(fresh)
    assert reused['years'][0]['operating']==first['years'][0]['operating']
    assert reused['years'][0]['repayment']>first['years'][0]['repayment']
    finance.forecast(franchise,{**changed,'population':60000},royalty_cache=cache)
    assert len(calls)==180
