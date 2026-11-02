from urllib.parse import urlparse

import pytest
from flask import Flask

from app.module_switching import requested_module, MODULE_DESTINATIONS
from test_native_territory import app
from test_access_profiles import setup_accounts, sign_in, call


@pytest.mark.parametrize('module,endpoint', [
    ('attendance', '/launch/attendance'), ('heatmap', '/heat-map/'),
    ('manuals', '/manuals/'), ('claims', '/launch/claims'), ('insurance', '/launch/insurance'),
])
def test_switch_returns_to_the_requested_module_with_new_branch_scope(app, module, endpoint):
    identity, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    sign_in(client, f'branch:{profile.id}')
    page = call(client, 'get', f'/profiles?module={module}')
    assert f'name="return_module" value="{module}"' in page.text
    assert MODULE_DESTINATIONS[module][0] in page.text
    with client.session_transaction() as session:
        token = session['registration_csrf']
    response = call(client, 'post', '/profiles', data={'profile_key': f'linked:{profile.id}:{branches[1].id}', 'csrf_token': token, 'return_module': module})
    assert response.status_code == 302 and urlparse(response.location).path == endpoint
    assert call(client, 'get', '/_profile_test/who').json['ids'] == [branches[1].id]


def test_module_return_does_not_bypass_csrf_or_profile_grants(app):
    _, _, branches, profile = setup_accounts(app)
    client = app.test_client(); sign_in(client, f'branch:{profile.id}')
    assert call(client, 'post', '/profiles', data={'profile_key': f'linked:{profile.id}:{branches[1].id}', 'return_module': 'attendance'}).status_code == 400
    call(client, 'get', '/profiles?module=attendance')
    with client.session_transaction() as session:
        token = session['registration_csrf']
    assert call(client, 'post', '/profiles', data={'profile_key': 'branch:999999', 'csrf_token': token, 'return_module': 'attendance'}).status_code == 403


def test_attendance_context_survives_an_expired_login(app):
    _, _, branches, profile = setup_accounts(app)
    client = app.test_client()
    page = call(client, 'post', '/login?next=%2Fprofiles%3Fmodule%3Dattendance', data={'email': 'deon@martinsdirect.com', 'password': 'one-login-password'})
    assert page.status_code == 200 and 'name="return_module" value="attendance"' in page.text
    with client.session_transaction() as session:
        token = session['profile_login']['token']
    result = call(client, 'post', '/login', data={'profile_key': f'linked:{profile.id}:{branches[1].id}', 'csrf_token': token, 'return_module': 'attendance'})
    assert urlparse(result.location).path == '/launch/attendance'


@pytest.mark.parametrize('target', ['https://evil.example/profiles?module=attendance', '//evil.example/profiles?module=attendance', '/unknown?module=attendance', 'http://['])
def test_untrusted_next_url_is_never_a_redirect_target(target):
    with Flask(__name__).test_request_context('/login', query_string={'next': target}):
        assert requested_module() == ''


def test_heatmap_header_preserves_module_when_switching(app):
    identity, owner, branches, profile = setup_accounts(app)
    from app.extensions import db
    from app.models import Permission
    from app.module_licensing import start_trial
    owner.roles[0].permissions.append(Permission(module='heat_map', action='view', code='heat_map:view', label='Heat map'))
    db.session.commit(); start_trial(owner); db.session.commit()
    client = app.test_client(); sign_in(client, f'branch:{profile.id}')
    page = call(client, 'get', '/heat-map/')
    assert page.status_code == 200
    assert 'href="/profiles?module=heatmap"' in page.text
    assert 'Switch user / branch' in page.text
