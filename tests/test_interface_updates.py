from datetime import date
from decimal import Decimal
import pytest

from test_native_territory import app, franchise, login
from app.extensions import db
from app.models import Franchise, MonthlyFigure, FranchiseTarget, User
from app.performance.service import leaderboard_rows, PERFORMANCE_METRICS
from app.year_comparison import yearly_indicator


def test_login_failure_success_and_logout_do_not_queue_notifications(app, franchise):
    login(app, franchise)
    user = User.query.first()
    user.set_password('Correct-test-password')
    db.session.commit()
    client = app.test_client()
    failed = client.post('/login', data={'email': user.email, 'password': 'wrong'})
    assert failed.status_code == 200
    with client.session_transaction() as session:
        assert '_user_id' not in session
        assert not session.get('_flashes')
    success = client.post('/login', data={'email': user.email, 'password': 'Correct-test-password'})
    assert success.status_code == 302
    with client.session_transaction() as session:
        assert session['_user_id'] == str(user.id)
        assert not session.get('_flashes')
    assert client.get('/logout').status_code == 302
    with client.session_transaction() as session:
        assert '_user_id' not in session
        assert not session.get('_flashes')


@pytest.mark.parametrize('role, show_status_cards', [('Admin', True), ('Franchise User', False),
                                                   ('Franchise Manager', False), ('Read Only User', False)])
def test_royalty_status_cards_only_in_head_office_view(app, franchise, role, show_status_cards):
    client = login(app, franchise, role=role)
    from app.models import Permission
    user = User.query.first()
    user.roles[0].permissions.append(Permission(module='Royalties', action='view', code='royalties:view', label='View royalties'))
    db.session.commit()
    response = client.get('/royalties/')
    assert response.status_code == 200
    for label in ('Unapproved', 'Approved', 'Locked'):
        assert (f'<span>{label}</span>' in response.text) is show_status_cards
    assert '<span>Total Royalties</span>' in response.text


def test_yearly_arrows_compare_actuals_without_changing_rank_or_using_future_months(app, franchise):
    other = Franchise(business_name='Other franchise', is_performance_active=True, agreement_start_date=date(2020, 1, 1))
    db.session.add(other)
    db.session.flush()
    for f, current, previous in [(franchise, 120, 100), (other, 200, 50)]:
        db.session.add_all([MonthlyFigure(franchise_id=f.id, month=1, year=2026, cash=current),
                            MonthlyFigure(franchise_id=f.id, month=1, year=2025, cash=previous),
                            MonthlyFigure(franchise_id=f.id, month=2, year=2025, cash=999999),
                            FranchiseTarget(franchise_id=f.id, month=1, year=2026, metric='cash', target_value=100)])
    db.session.commit()
    with app.test_request_context('/performance/?month=1&year=2026'):
        rows = leaderboard_rows('cash', 1, 2026, [franchise.id, other.id])
    assert [r['franchise_id'] for r in rows] == [other.id, franchise.id]
    assert [r['rank'] for r in rows] == [1, 2]
    assert all(r['yearly_direction'] == 'up' for r in rows)
    assert rows[1]['yearly_change'] == Decimal('20')


@pytest.mark.parametrize('current, previous, expected', [(90, 100, 'down'), (100, 100, 'same'),
                                                        (10, 0, 'up'), (0, 10, 'down')])
def test_arrows_cover_increase_decline_zero_and_no_change(current, previous, expected):
    assert yearly_indicator({'cash': current}, {'cash': previous}, {}, 'cash')['yearly_direction'] == expected


def test_missing_year_is_neutral_and_overall_normalizes_different_units():
    assert yearly_indicator({'cash': 100}, None, {}, 'cash')['yearly_direction'] == 'unavailable'
    weights = {key: value['weight'] for key, value in PERFORMANCE_METRICS.items()}
    previous = dict(cash=100000, sales=100000, insurance_premiums=100000, joinings=10, funerals=10)
    current = {key: value * 2 for key, value in previous.items()}
    assert yearly_indicator(current, previous, weights)['yearly_direction'] == 'up'


def test_all_templates_compile(app):
    for name in app.jinja_env.list_templates():
        app.jinja_env.get_template(name)
