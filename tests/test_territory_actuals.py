from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from test_native_territory import app, franchise, login, payload, HEADERS, inputs
from app.extensions import db
from app.models import MonthlyFigure, Franchise, Role, Permission
from app.franchise.territory_actuals import baseline
from app.franchise.territory_finance import forecast


def grant_monthly():
    role=Role.query.filter_by(name='Admin').one()
    role.permissions.append(Permission(module='Monthly Figures',action='view',code='monthly_figures:view',label='View'))
    db.session.commit()


def seed(franchise_id,today):
    end=today.year*12+today.month-1
    rows=[]
    for offset,funerals in [(1,3),(2,5),(3,7)]:
        period=end-offset
        rows.append(MonthlyFigure(franchise_id=franchise_id,year=period//12,month=period%12+1,status='Approved',
            funeral_receipts=funerals*3000,number_of_funerals=funerals,insurance_receipts=1000,insurance_payover=800,admin_fee=200))
    db.session.add_all(rows);db.session.commit()
    return rows


def test_franchise_approved_baseline_has_no_missing_month_zero_imputation(app,franchise):
    today=date(2026,10,20)
    rows=seed(franchise.id,today)
    other=Franchise(business_name='Other');db.session.add(other);db.session.flush()
    db.session.add_all([MonthlyFigure(franchise_id=other.id,year=2026,month=9,status='Approved',number_of_funerals=999),
        MonthlyFigure(franchise_id=franchise.id,year=2026,month=6,status='Draft',number_of_funerals=999),
        MonthlyFigure(franchise_id=franchise.id,year=2026,month=10,status='Approved',number_of_funerals=999)])
    db.session.commit()
    result=baseline(franchise.id,today)
    assert result['observed_months']==3
    assert result['window_start']=='2025-10' and result['window_end']=='2026-09'
    assert result['proposed_inputs']['recorded_monthly_services']==5
    assert result['proposed_inputs']['burial_price']==3000
    assert result['proposed_inputs']['expense_percent']==70
    assert result['proposed_inputs']['repayment_months']==60 and result['proposed_inputs']['interest_percent']==0
    assert len(result['source_rows'])==3
    assert not result['proposed_inputs']['confirmed']
    duplicate=MonthlyFigure(franchise_id=franchise.id,year=2026,month=9,status='Locked')
    db.session.add(duplicate);db.session.commit()
    with pytest.raises(ValueError,match='Duplicate'):baseline(franchise.id,today)


def test_seventy_percent_expenses_do_not_double_count_manual_costs(franchise):
    a=inputs(demand_basis='franchise_history',recorded_monthly_services=10,burial_price=20000,
        expense_mode='percentage',expense_percent=70,fixed_cost=999999,owner_salary=0)
    row=forecast(franchise,a)['months'][0]
    assert row['revenue']==200000 and row['direct']==0 and row['overhead']==128000
    assert row['royalty']==12000 and row['operating']==60000 and row['repayment']==2500
    assert row['direct']+row['overhead']+row['royalty']==140000
    assert row['retained']==57500
    manual=forecast(franchise,{**a,'expense_mode':'manual','fixed_cost':60000})['months'][0]
    assert manual['direct']==85000 and manual['overhead']==60000


def test_actuals_permission_reference_changes_and_report_snapshot(app,franchise):
    today=datetime.now(timezone(timedelta(hours=2))).date()
    rows=seed(franchise.id,today)
    client=login(app,franchise)
    base=f'/franchise/{franchise.id}/territory'
    assert client.post(base+'/franchise-figures',json={},headers=HEADERS).status_code==403
    grant_monthly()
    response=client.post(base+'/franchise-figures',json={},headers=HEADERS)
    assert response.status_code==200
    actuals=response.json
    data=payload();data['inputs'].update(actuals['proposed_inputs']);data['financial_baseline']=actuals['fingerprint']
    assert client.post(base+'/save',json={'version':0,'payload':data},headers=HEADERS).status_code==200
    report=client.get(base+'/report').get_data(as_text=True)
    assert 'Recorded franchise financial baseline' in report and 'Operating expenses: 70.00%' in report
    rows[0].funeral_receipts=Decimal(99999);db.session.commit()
    assert client.post(base+'/calculate',json=data,headers=HEADERS).status_code==400
    # Old saved report retains the financial evidence used, even after records change.
    assert '99999' not in client.get(base+'/report').get_data(as_text=True)
    role=Role.query.filter_by(name='Admin').one()
    role.permissions=[p for p in role.permissions if p.code!='monthly_figures:view'];db.session.commit()
    assert client.get(base).status_code==403
    assert client.get(base+'/report').status_code==403
    assert client.get(base+'/export').status_code==403


def test_no_unverified_recorded_volume_without_internal_reference(app,franchise):
    client=login(app,franchise)
    data=payload();data['inputs']['demand_basis']='franchise_history'
    assert client.post(f'/franchise/{franchise.id}/territory/calculate',json=data,headers=HEADERS).status_code==400


def test_contractual_royalty_above_expense_budget_never_creates_negative_other_costs(franchise):
    franchise.minimum_royalty_is_none=False
    franchise.minimum_royalty_amount=150000
    db.session.commit()
    result=forecast(franchise,inputs(expense_mode='percentage',expense_percent=70))
    assert result['months'][0]['overhead']==0
    assert result['months'][0]['royalty']==150000
    assert not result['affordable'] and any('royalty exceeds' in e for e in result['errors'])


def test_percentage_expenses_ignore_unused_manual_salary_validation(franchise):
    data=inputs(expense_mode='percentage',expense_percent=70,fixed_cost=0,
        owner_salary=25000,salary_in_fixed=True)
    result=forecast(franchise,data)
    row=result['months'][0]
    assert row['direct']==0
    assert row['overhead']+row['royalty']==row['revenue']*Decimal('0.70')
    with pytest.raises(ValueError,match='Owner salary exceeds'):
        forecast(franchise,{**data,'expense_mode':'manual'})
