from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
from flask import Flask
from itsdangerous import URLSafeTimedSerializer
from app.attendance_launch import routes


def test_attendance_handoff_uses_holder_group_without_expanding_active_branch(monkeypatch):
    first = SimpleNamespace(id=1, business_name='Branch One', vat_number='4000000000')
    second = SimpleNamespace(id=2, business_name='Branch Two', vat_number='4000000000')
    owner = SimpleNamespace(assigned_franchises=[first, second])
    user = SimpleNamespace(email='owner@example.test', full_name='Example Owner', roles=[SimpleNamespace(name='Franchise User')], principal_account=owner,
                           has_permission=lambda code: code == 'attendance:view')
    monkeypatch.setattr(routes, 'current_user', user)
    monkeypatch.setattr(routes, 'get_accessible_franchises', lambda: [first])
    monkeypatch.setenv('ATTENDANCE_APP_URL', 'https://attendance.example.test')
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET', 'test-only-launch-secret')
    app = Flask(__name__)
    with app.test_request_context('/launch/attendance'):
        response = routes.launch.__wrapped__()
    token = parse_qs(urlparse(response.location).query)['martins_launch'][0]
    data = URLSafeTimedSerializer('test-only-launch-secret', salt='martins-attendance-launch-v1').loads(token)
    assert data['franchises'] == ['Branch One']
    assert data['branch_scope'] == [{'id':1,'name':'Branch One','vat_number':'4000000000'}, {'id':2,'name':'Branch Two','vat_number':'4000000000'}]
    assert data['is_admin'] is False


def test_nonowner_launch_does_not_inherit_a_principals_group(monkeypatch):
    user = SimpleNamespace(email='staff@example.test', full_name='Example Staff', roles=[SimpleNamespace(name='Read Only User')],
                           has_permission=lambda code: code == 'attendance:view')
    monkeypatch.setattr(routes, 'current_user', user)
    monkeypatch.setattr(routes, 'get_accessible_franchises', lambda: [])
    monkeypatch.setenv('ATTENDANCE_APP_URL', 'https://attendance.example.test')
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET', 'test-only-launch-secret')
    with Flask(__name__).test_request_context('/launch/attendance'):
        response = routes.launch.__wrapped__()
    token = parse_qs(urlparse(response.location).query)['martins_launch'][0]
    data = URLSafeTimedSerializer('test-only-launch-secret', salt='martins-attendance-launch-v1').loads(token)
    assert data['branch_scope'] == []
