from html.parser import HTMLParser
from decimal import Decimal

from test_native_territory import app
from test_access_profiles import setup_accounts, sign_in, call


class GraphOptions(HTMLParser):
    def __init__(self):
        super().__init__(); self.in_select = False; self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select': self.in_select = attrs.get('name') == 'franchise_id'
        if tag == 'option' and self.in_select: self.ids.append(attrs.get('value'))

    def handle_endtag(self, tag):
        if tag == 'select': self.in_select = False


def branches_with_recent_data(app):
    from app.extensions import db
    from app.models import Franchise, MonthlyFigure
    _, _, branches, profile = setup_accounts(app, office_role='Admin')
    branches[0].business_name = 'Middelburg'
    branches[1].business_name = 'Kwa Mahlangu'
    for branch in branches: branch.is_performance_active = False
    stale = Franchise(business_name='Old figures only', is_performance_active=False)
    zero = Franchise(business_name='Zero figures only', is_performance_active=False)
    db.session.add_all([stale, zero]); db.session.flush()
    db.session.add_all([
        MonthlyFigure(franchise_id=branches[0].id, year=2026, month=10, funeral_receipts=1000, cash=1000),
        MonthlyFigure(franchise_id=branches[1].id, year=2026, month=9, funeral_receipts=2000, cash=2000),
        MonthlyFigure(franchise_id=branches[2].id, year=2026, month=10, funeral_receipts=9000, cash=9000),
        MonthlyFigure(franchise_id=stale.id, year=2026, month=4, funeral_receipts=3000, cash=3000),
        MonthlyFigure(franchise_id=zero.id, year=2026, month=10),
    ]); db.session.commit()
    return branches, profile, stale, zero


def test_dashboard_active_branches_are_selectable_with_real_graph_values(app):
    from app.dashboard.routes import split_franchises_by_recent_data
    branches, _, stale, zero = branches_with_recent_data(app)
    active, _, period = split_franchises_by_recent_data([*branches, stale, zero])
    assert {row.id for row in active} == {row.id for row in branches}
    assert period == 'Last 6 months ending 2026-10'
    client = app.test_client(); sign_in(client, 'head_office')
    page = call(client, 'get', '/performance/graphs?month=10&year=2026')
    assert page.status_code == 200
    options = GraphOptions(); options.feed(page.text)
    assert {str(row.id) for row in active}.issubset(options.ids)
    assert str(stale.id) not in options.ids and str(zero.id) not in options.ids
    for branch, value in [(branches[0], 1000), (branches[1], 2000)]:
        response = call(client, 'get', f'/performance/graphs/data?franchise_id={branch.id}&month=10&year=2026&periods=6&metric=cash')
        assert response.status_code == 200
        assert sum(point['actual'] for point in response.json['graph_data']['actual_vs_target']) == value
    combined = call(client, 'get', '/performance/graphs/data?franchise_id=all&month=10&year=2026&periods=6&metric=cash')
    assert sum(point['actual'] for point in combined.json['graph_data']['actual_vs_target']) == 12000
    assert all(row.is_performance_active is False for row in branches)


def test_franchise_profile_can_graph_its_active_branch_without_expanding_access(app):
    branches, profile, _, _ = branches_with_recent_data(app)
    client = app.test_client(); sign_in(client, f'branch:{profile.id}')
    page = call(client, 'get', '/performance/graphs?month=10&year=2026')
    assert page.status_code == 200
    options = GraphOptions(); options.feed(page.text)
    assert options.ids == [str(branches[0].id)]
    data = call(client, 'get', f'/performance/graphs/data?franchise_id={branches[0].id}&month=10&year=2026&metric=cash')
    assert data.status_code == 200
    assert sum(point['actual'] for point in data.json['graph_data']['actual_vs_target']) == 1000
    for path in ['/performance/graphs', '/performance/graphs/data']:
        assert call(client, 'get', path + f'?franchise_id={branches[1].id}&month=10&year=2026').status_code == 403
        assert call(client, 'get', path + f'?franchise_id={branches[2].id}&month=10&year=2026').status_code == 403
