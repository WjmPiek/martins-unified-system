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


def test_popup_shows_only_chooser_and_blocks_inactive_module(app):
    _, owner, branches, profile = setup_accounts(app)
    client = app.test_client(); sign_in(client, f'branch:{profile.id}')
    page = call(client, 'get', '/profiles?module=heatmap&popup=1')
    assert page.status_code == 200
    assert 'Your approved branches' in page.text and 'disabled' in page.text
    assert 'topbar-user' not in page.text and 'app-sidebar' not in page.text
    with client.session_transaction() as session:
        token = session['registration_csrf']
        previous = session['access_profile_key']
    result = call(client, 'post', '/profiles', data={'popup':'1', 'return_module':'heatmap', 'profile_key':f'linked:{profile.id}:{branches[1].id}', 'csrf_token':token})
    assert result.status_code == 403
    with client.session_transaction() as session:
        assert session['access_profile_key'] == previous


@pytest.mark.parametrize('module,path', [('heatmap','/heat-map/'),('attendance','/launch/attendance')])
def test_popup_switch_returns_module_to_original_window(app, module, path):
    from app.extensions import db
    from app.module_licensing import start_trial
    _, owner, branches, profile = setup_accounts(app)
    start_trial(owner); db.session.commit()
    client = app.test_client(); sign_in(client, f'branch:{profile.id}')
    page = call(client, 'get', f'/profiles?module={module}&popup=1')
    assert 'Your approved branches' in page.text
    with client.session_transaction() as session:
        token = session['registration_csrf']
    data={'popup':'1', 'return_module':module, 'profile_key':f'linked:{profile.id}:{branches[1].id}', 'csrf_token':token, 'opener_origin':'https://evil.example'}
    result = call(client, 'post', '/profiles', data=data)
    assert result.status_code == 200 and 'martins:branch-switched' in result.text
    assert path in result.text and 'evil.example' not in result.text
    assert call(client,'get','/_profile_test/who').json['ids'] == [branches[1].id]


def test_popup_return_survives_sign_in(app):
    _, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    result = call(client,'post','/login?next=%2Fprofiles%3Fmodule%3Dattendance%26popup%3D1%26opener_origin%3Dhttps%253A%252F%252Fname-attendance-register-frontend.onrender.com',data={'email':'deon@martinsdirect.com','password':'one-login-password'})
    assert 'name="popup" value="1"' in result.text
    with client.session_transaction() as session:
        token=session['profile_login']['token']
    result=call(client,'post','/login',data={'profile_key':f'branch:{profile.id}','csrf_token':token,'return_module':'attendance','popup':'1','opener_origin':'https://name-attendance-register-frontend.onrender.com'})
    assert '/profiles?' in result.location and 'popup=1' in result.location
    assert 'opener_origin=https://name-attendance-register-frontend.onrender.com' in result.location
