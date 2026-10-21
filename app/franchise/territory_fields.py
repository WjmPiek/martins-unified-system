"""Consistent labels, units and formatting for territory inputs and saved reports."""
from decimal import Decimal, InvalidOperation
from app.franchise.territory_finance import DEFAULTS, number, money

LABELS = {
'demand_basis':'Demand calculation','recorded_monthly_services':'Recorded funerals per month',
'population':'Estimated population (people)','mortality_per_1000':'Annual mortality rate (deaths per 1,000 people)',
'household_size':'Average household size (people)','market_share':'Expected market share',
'cremation_share':'Cremation share of services','burial_price':'Cash burial package price',
'burial_cost':'Direct cost per burial','cremation_price':'Cash cremation package price','cremation_cost':'Direct cost per cremation',
'expense_mode':'Operating expense calculation','expense_percent':'Total operating expenses',
'fixed_cost':'Other fixed operating costs per month (excluding owner salary)',
'owner_salary':'Owner salary per month','salary_in_fixed':'Owner salary included in fixed costs',
'policy_receipts':'Insurance premiums collected per month','policy_payover':'Insurer payover per month',
'policy_commission':'Additional policy commission per month','policy_direct_cost':'Direct policy costs per month',
'other_sales':'Other sales per month','reserve':'Monthly cash reserve','annual_finance':'Other loan principal repayments per year',
'annual_depreciation':'Annual depreciation (existing assets)','annual_amortisation':'Annual amortisation (existing rights)','annual_interest':'Other borrowing interest per year',
'annual_capex':'Annual capital expenditure','annual_working':'Additional working capital per year','tax_percent':'Planning income-tax rate on profit before tax',
'discount_percent':'Discount rate for optional contribution comparison',
'territory_amortisation_months':'Territory-right amortisation term (months; 0 = not modelled)',
'commercial_category':'Martins commercial fee category','fee_method':'Territory fee calculation','territory_fee':'Territory purchase fee (excluding VAT)',
'joining_fee':'Joining fee','setup':'Initial setup costs','working_capital':'Opening working capital',
'payment_mode':'Territory payment option','repayment_months':'Territory repayment term (months)',
'interest_percent':'Annual interest rate','vat_percent':'VAT rate','vat_recoverable':'VAT is recoverable',
'start_year':'Projection start year','start_month':'Projection start month','volume_growth':'Annual service volume growth',
'price_growth':'Annual service price growth','cost_growth':'Annual cost growth','ramp_months':'Initial ramp-up period (months)',
'target_margin':'Target operating profit margin','safety_buffer':'Downside investment safety buffer',
'confirmed':'Financial assumptions reviewed'}
CURRENCY = set('burial_price burial_cost cremation_price cremation_cost fixed_cost owner_salary policy_receipts policy_payover policy_commission policy_direct_cost other_sales reserve annual_finance annual_capex annual_working annual_depreciation annual_amortisation annual_interest territory_fee joining_fee setup working_capital'.split())
PERCENT = set('market_share cremation_share expense_percent tax_percent interest_percent vat_percent volume_growth price_growth cost_growth target_margin safety_buffer discount_percent'.split())
INTEGER = set('population repayment_months start_year start_month ramp_months territory_amortisation_months'.split())
CHOICES = {
'commercial_category':{'small':'Small area','standard':'Standard area','large':'Large area'},
'demand_basis':{'territory':'Selected territory (automatic)','franchise_history':'Recorded franchise activity'},
'expense_mode':{'manual':'Manually entered expenses','percentage':'Percentage of revenue'},
'payment_mode':{'instalments':'Monthly instalments','upfront':'Upfront payment'},
'fee_method':{'commercial':'Martins commercial fee','scorecard':'Existing commercial scorecard method','fair_territory':'Optional Territory Fee — valuation and affordability','profit_margin':'Five-year profit-margin method (saved legacy selection)'}}
MONTHS = dict(enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1))


def field_spec(key):
    kind='currency' if key in CURRENCY else 'percentage' if key in PERCENT else 'integer' if key in INTEGER else 'decimal'
    return dict(label=LABELS[key],kind=kind,step='1' if key in INTEGER else '0.000001' if key=='mortality_per_1000' else '0.01',
        minimum=-50 if key.endswith('growth') else 2000 if key=='start_year' else 1 if key in {'repayment_months','start_month'} else 0,
        maximum=50 if key.endswith('growth') else 30 if key=='vat_percent' else 100 if key in PERCENT else 2200 if key=='start_year' else 12 if key=='start_month' else 600 if key=='territory_amortisation_months' else 60 if key in {'repayment_months','ramp_months'} else 50 if key=='mortality_per_1000' else 1000000000000)


def field_value(key,value,inputs=None):
    if value is None:return 'Not recorded'
    if isinstance(value,bool):return 'Yes' if value else 'No'
    if key in CHOICES:return CHOICES[key].get(str(value),str(value))
    try:
        number=Decimal(str(value))
        if not number.is_finite():return 'Not recorded'
        if key=='start_month':return MONTHS.get(int(number),str(value))
        if key in CURRENCY:return 'R '+format(number,',.2f')
        if key in PERCENT:return format(number,',.2f')+'%'
        if key=='start_year':return str(int(number))
        if key in INTEGER:return format(number,',.0f')
        if key=='mortality_per_1000':return format(number,'.6f')+' deaths per 1,000 people per year'
        return format(number,',.2f')
    except (InvalidOperation,ValueError,TypeError):return str(value)


def separate_owner_salary(raw):
    """Convert a legacy inclusive fixed-cost amount once; current forms send False."""
    result=dict(raw)
    included=result.get('salary_in_fixed',DEFAULTS['salary_in_fixed'])
    if type(included) is not bool:raise ValueError('Invalid legacy salary inclusion flag')
    if included:
        fixed=number(result.get('fixed_cost',DEFAULTS['fixed_cost']),'Other fixed operating costs')
        salary=number(result.get('owner_salary',DEFAULTS['owner_salary']),'Owner salary')
        if fixed<0 or salary<0:raise ValueError('Costs and owner salary cannot be negative')
        if fixed<salary and result.get('expense_mode',DEFAULTS['expense_mode'])=='manual':
            raise ValueError('Owner salary exceeds the previous fixed-cost amount; review the manual cost entries')
        result['fixed_cost']=str(money(max(Decimal(0),fixed-salary)))
    result['salary_in_fixed']=False
    return result


def field_label(key,inputs):
    if 'annual_depreciation' not in inputs:
        if key=='tax_percent':return 'Planning tax rate on operating profit'
        if key=='annual_finance':return 'Other annual finance payments'
    if key=='fixed_cost' and inputs.get('salary_in_fixed',True):
        return 'Fixed operating costs per month (including owner salary)'
    return LABELS[key]


def display_date(value):
    """Human-readable SAST dates; preserve quoted source periods verbatim."""
    from datetime import datetime, timezone, timedelta
    import re
    if value is None:return 'Not recorded'
    text=str(value)
    try:
        if re.fullmatch(r'\d{4}-\d{2}',text):
            return datetime.strptime(text,'%Y-%m').strftime('%B %Y')
        if not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[T ])',text):return text
        stamp=value if isinstance(value,datetime) else datetime.fromisoformat(text.replace('Z','+00:00'))
        if isinstance(value,datetime) or len(text)>10:
            stamp=stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            return stamp.astimezone(timezone(timedelta(hours=2))).strftime('%d %B %Y, %H:%M SAST')
        return stamp.strftime('%d %B %Y')
    except ValueError:return text
