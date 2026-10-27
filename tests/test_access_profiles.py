import importlib.util
from pathlib import Path

import pytest
from flask import g, jsonify
from flask_login import current_user, login_required
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_native_territory import app
from app.extensions import db
from app.models import User, Role, Permission, Franchise, UserAccessProfile, user_franchises, AuditLog
from app.access_profiles import choices, grant_profile


def setup_accounts(app, office_role='Finance Assistant', email='deon@martinsdirect.com'):
    codes = ['dashboard:view', 'performance:view', 'franchise_details:view', 'franchise_management:view', 'monthly_figures:import', 'users:view', 'users:edit']
    permissions = {code: Permission(module=code.split(':')[0], action=code.split(':')[1], code=code, label=code) for code in codes}
    office = Role(name=office_role, permissions=list(permissions.values()))
    holder = Role(name='Franchise User', permissions=[permissions['dashboard:view'], permissions['performance:view'], permissions['franchise_details:view']])
    identity = User(name='Wjm' if office_role == 'Admin' else 'Deon', surname='Piek' if office_role == 'Admin' else 'Koekemoer', email=email, roles=[office])
    identity.set_password('one-login-password')
    branches = [Franchise(business_name=name, is_performance_active=True) for name in ['Krugersdorp (F)', 'Benoni (F)', 'Unrelated branch']]
    owner = User(name='Krugersdorp', surname='User', email='krugersdorp@martinsdirect.com', roles=[holder], assigned_franchises=branches[:2])
    owner.set_password('different-owner-password')
    db.session.add_all([identity, owner, *branches])
    db.session.flush()
    db.session.execute(user_franchises.update().where(user_franchises.c.user_id == owner.id).where(user_franchises.c.franchise_id == branches[0].id).values(is_primary=True))
    profile = grant_profile(identity, branches[0], owner, identity.id)
    db.session.commit()

    @app.get('/_profile_test/who')
    @login_required
    def who():
        from app.admin.routes import can_view_imports_data
        return jsonify(id=current_user.id, identity=getattr(current_user, 'identity_account', current_user).id,
            roles=[r.name for r in current_user.roles], ids=[b.id for b in current_user.accessible_franchises()],
            is_admin=current_user.is_admin_user(), import_allowed=can_view_imports_data(),
            active_branch=current_user.assigned_franchise_id())
    return identity, owner, branches, profile


def call(client, method, path, **kwargs):
    # Shared fixture holds an application context across requests.
    for key in list(g):
        g.pop(key, None)
    return getattr(client, method)(path, **kwargs)


def sign_in(client, key=None, email='deon@martinsdirect.com'):
    response = call(client, 'post', '/login', data={'email': email.upper(), 'password': 'one-login-password'})
    if key:
        with client.session_transaction() as session:
            pending = session.get('profile_login')
        if not pending:
            return response
        token = pending['token']
        return call(client, 'post', '/login', data={'profile_key': key, 'csrf_token': token})
    return response


def switch(client, key):
    call(client, 'get', '/profiles')
    with client.session_transaction() as session:
        token = session['registration_csrf']
    return call(client, 'post', '/profiles', data={'profile_key': key, 'csrf_token': token})


def test_one_email_selects_head_office_and_every_linked_branch(app):
    identity, owner, branches, profile = setup_accounts(app)
    original_hashes = identity.password_hash, owner.password_hash
    client = app.test_client()
    page = sign_in(client)
    assert page.status_code == 200
    assert 'Head Office' in page.text and 'Krugersdorp' in page.text and 'Benoni' in page.text
    assert 'Unrelated branch' not in page.text
    with client.session_transaction() as session:
        assert '_user_id' not in session
    assert sign_in(client, 'head_office').location.endswith('/admin/imports')
    info = call(client, 'get', '/_profile_test/who').json
    assert info['roles'] == ['Finance Assistant'] and info['import_allowed']
    assert len(info['ids']) == 3
    office_page = call(client, 'get', '/admin/imports')
    assert office_page.status_code == 200 and 'Import Month-End PDF' in office_page.text
    assert switch(client, f'branch:{profile.id}').status_code == 302
    info = call(client, 'get', '/_profile_test/who').json
    assert info['identity'] == identity.id and info['id'] == owner.id
    assert info['roles'] == ['Franchise User'] and info['ids'] == [branches[0].id]
    assert not info['import_allowed'] and not info['is_admin']
    assert call(client, 'get', '/admin/imports').status_code == 403
    assert call(client, 'get', '/monthly-figures/import').status_code == 403
    assert switch(client, f'linked:{profile.id}:{branches[1].id}').status_code == 302
    assert call(client, 'get', '/_profile_test/who').json['ids'] == [branches[1].id]
    assert identity.roles[0].name == 'Finance Assistant'
    assert original_hashes == (identity.password_hash, owner.password_hash)


def test_profile_switch_clears_context_and_http_caches(app):
    identity, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    sign_in(client, 'head_office')
    with client.session_transaction() as session:
        session['selected_franchise_id'] = branches[2].id
        session['territory_graph_context'] = {'user_id': identity.id, 'franchise_id': branches[2].id}
    switch(client, f'branch:{profile.id}')
    with client.session_transaction() as session:
        assert session['_user_id'] == str(identity.id)
        assert 'territory_graph_context' not in session
        assert session.get('selected_franchise_id') != branches[2].id
    response = call(client, 'get', '/_profile_test/who')
    assert 'no-store' in response.headers['Cache-Control'] and 'Cookie' in response.headers['Vary']
    assert switch(client, 'head_office').location.endswith('/admin/imports')
    details = AuditLog.query.filter_by(action='Switched profile').all()
    assert any(f'sign-in user #{identity.id}' in item.details and 'branch:' in item.details for item in details)


@pytest.mark.parametrize('change', ['revoke', 'unlink', 'inactive_identity', 'inactive_owner'])
def test_revoked_unlinked_and_disabled_profiles_stop_working_on_next_request(app, change):
    identity, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    sign_in(client, f'branch:{profile.id}')
    if change == 'revoke':
        profile.is_active = False
    elif change == 'unlink':
        owner.assigned_franchises = [branches[1]]
    elif change == 'inactive_identity':
        identity.is_active_account = False
    else:
        owner.is_active = False
    db.session.commit()
    assert call(client, 'get', '/_profile_test/who').status_code == 302


def test_unlink_one_group_branch_removes_only_that_choice(app):
    identity, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    sign_in(client, f'linked:{profile.id}:{branches[1].id}')
    owner.assigned_franchises = [branches[0]]
    db.session.commit()
    assert call(client, 'get', '/_profile_test/who').status_code == 302
    assert [item['franchise'].id for item in choices(identity) if item['franchise']] == [branches[0].id]


def test_pure_holder_uses_one_sign_in_for_royalty_links(app):
    _, owner, branches, _ = setup_accounts(app)
    owner.set_password('one-login-password')
    db.session.commit()
    client = app.test_client()
    response = sign_in(client, email=owner.email)
    assert 'Head Office —' not in response.text
    assert all(branch.business_name in response.text for branch in branches[:2])
    assert sign_in(client, f'own:{branches[1].id}', email=owner.email).status_code == 302
    assert call(client, 'get', '/_profile_test/who').json['ids'] == [branches[1].id]


def test_protected_admin_has_no_admin_or_import_access_in_holder_profile(app):
    identity, owner, branches, profile = setup_accounts(app, 'Admin', 'wjm@martinsdirect.com')
    client = app.test_client()
    sign_in(client, f'branch:{profile.id}', email=identity.email)
    info = call(client, 'get', '/_profile_test/who').json
    assert not info['is_admin'] and not info['import_allowed']
    assert call(client, 'get', '/admin/imports').status_code == 403
    assert call(client, 'get', f'/admin/users/{identity.id}/profiles').status_code == 403
    switch(client, 'head_office')
    assert call(client, 'get', '/admin/imports').status_code == 200
    assert identity.is_protected_admin and identity.has_role('Admin')


def test_profile_choice_requires_credentials_and_csrf_and_cannot_be_forged(app):
    identity, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    assert call(client, 'post', '/login', data={'profile_key': f'branch:{profile.id}', 'csrf_token': 'forged'}).status_code == 200
    with client.session_transaction() as session:
        assert '_user_id' not in session
    sign_in(client)
    with client.session_transaction() as session:
        token = session['profile_login']['token']
    assert call(client, 'post', '/login', data={'profile_key': 'branch:999999', 'csrf_token': token}).status_code == 403
    sign_in(client, 'head_office')
    assert call(client, 'post', '/profiles', data={'profile_key': f'branch:{profile.id}'}).status_code == 400
    assert call(client, 'get', '/_profile_test/who').json['roles'] == ['Finance Assistant']


def test_pending_login_rejects_password_changes(app):
    identity, _, _, _ = setup_accounts(app)
    client = app.test_client()
    sign_in(client)
    with client.session_transaction() as session:
        token = session['profile_login']['token']
    identity.set_password('new-password')
    db.session.commit()
    response = call(client, 'post', '/login', data={'profile_key': 'head_office', 'csrf_token': token})
    assert 'expired' in response.text
    with client.session_transaction() as session:
        assert '_user_id' not in session


def test_existing_finance_session_discards_old_franchise_view(app):
    identity, _, branches, _ = setup_accounts(app)
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(identity.id)
        session['_fresh'] = True
        session['franchise_view_mode'] = True
        session['selected_franchise_id'] = branches[2].id
    assert call(client, 'get', '/admin/imports').status_code == 200
    with client.session_transaction() as session:
        assert not session.get('franchise_view_mode')


def test_admin_can_link_existing_owner_without_changing_owner_or_identity_roles(app):
    identity, owner, branches, _ = setup_accounts(app)
    admin = User(name='Head', surname='Office', email='wjm@martinsdirect.com', roles=[Role(name='Admin')])
    admin.set_password('one-login-password')
    db.session.add(admin)
    db.session.commit()
    client = app.test_client()
    sign_in(client, 'head_office', email=admin.email)
    page = call(client, 'get', f'/admin/users/{identity.id}/profiles')
    assert page.status_code == 200
    with client.session_transaction() as session:
        token = session['registration_csrf']
    assert call(client, 'post', f'/admin/users/{identity.id}/profiles', data={'franchise_id': branches[1].id, 'csrf_token': token}).status_code == 400
    response = call(client, 'post', f'/admin/users/{identity.id}/profiles', data={'franchise_id': branches[1].id, 'ownership_confirmed': 'yes', 'csrf_token': token})
    assert response.status_code == 302
    assert User.query.count() == 3 and UserAccessProfile.query.count() == 2
    assert identity.roles[0].name == 'Finance Assistant' and owner.roles[0].name == 'Franchise User'


def test_registration_links_existing_finance_identity_and_owner_only_after_confirmation(app, monkeypatch):
    from test_registration_flow import apply, review
    from app.models import FranchiseRegistration
    identity, owner, branches, existing = setup_accounts(app)
    db.session.delete(existing)
    db.session.commit()
    monkeypatch.setattr('app.registration.notify', lambda *args: False)
    client = app.test_client()
    assert apply(client, email=identity.email, business_name='Krugersdorp (F)').status_code == 302
    application = FranchiseRegistration.query.one()
    admin = User(name='Head', surname='Office', email='wjm@martinsdirect.com', roles=[Role(name='Admin')], password_hash='x')
    db.session.add(admin)
    db.session.commit()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin.id)
        session['_fresh'] = True
    original_hash = identity.password_hash
    review(client, application, franchise_id=str(branches[0].id))
    assert application.status == 'pending'
    review(client, application, franchise_id=str(branches[0].id), link_existing_profile='yes')
    assert application.status == 'approved' and application.user_id == identity.id
    assert UserAccessProfile.query.one().principal_user_id == owner.id
    assert User.query.count() == 3 and identity.password_hash == original_hash
    assert identity.roles[0].name == 'Finance Assistant'


def test_graph_data_and_franchise_pages_follow_selected_profile(app, monkeypatch):
    identity, owner, branches, profile = setup_accounts(app)
    seen = []
    def graph(franchise_id, *args, **kwargs):
        seen.append(franchise_id)
        return dict(metric_label='Cash', metric_key='cash', actual_vs_target=[{'actual': 123, 'target': 100}])
    monkeypatch.setattr('app.performance.routes.graph_engine_payload', graph)
    monkeypatch.setattr('app.performance.routes.graph_engine_payload_for_franchises', lambda ids, *a, **k: {'branch_ids': ids})
    client = app.test_client()
    sign_in(client, 'head_office')
    office = call(client, 'get', '/performance/graphs/data?month=9&year=2026&franchise_id=all')
    assert office.status_code == 200 and set(office.json['graph_data']['branch_ids']) == {branch.id for branch in branches}
    switch(client, f'linked:{profile.id}:{branches[1].id}')
    graph_page = call(client, 'get', '/performance/graphs')
    assert graph_page.status_code == 200 and 'All Franchise Users Combined' not in graph_page.text
    own = call(client, 'get', f'/performance/graphs/data?month=9&year=2026&franchise_id={branches[1].id}')
    assert own.status_code == 200 and own.json['graph_data']['actual_vs_target'][0]['actual'] == 123
    assert seen == [branches[1].id]
    assert call(client, 'get', f'/performance/graphs/data?franchise_id={branches[2].id}').status_code == 403
    assert call(client, 'get', f'/franchise/details?franchise_id={branches[0].id}').status_code == 403
    assert call(client, 'get', f'/franchise/details?franchise_id={branches[1].id}').status_code == 200


def test_royalty_summary_retains_original_group_while_general_scope_is_one_branch(app):
    from app.access_profiles import resolve
    from app.royalties.routes import get_ordered_linked_franchises_for_user
    identity, owner, branches, profile = setup_accounts(app)
    user = resolve(identity, f'linked:{profile.id}:{branches[1].id}')
    assert user.accessible_franchises() == [branches[1]]
    assert [item.id for item in get_ordered_linked_franchises_for_user(user)] == [branch.id for branch in branches[:2]]
    assert owner.assigned_franchise_id() == branches[0].id


def test_finance_manager_global_links_do_not_become_holder_profiles(app):
    identity, owner, branches, profile = setup_accounts(app, office_role='Finance Manager')
    identity.assigned_franchises = branches
    db.session.commit()
    options = choices(identity)
    assert len(options) == 3
    assert all(item['franchise'] != branches[2] for item in options)


def load_migration(name):
    path = Path(__file__).parents[1] / 'migrations/versions' / name
    spec = importlib.util.spec_from_file_location(name.replace('.py', ''), path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_profile_migrations_preserve_passwords_roles_and_group_links():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY,email VARCHAR,password_hash VARCHAR,is_active BOOLEAN,is_active_account BOOLEAN)')
        connection.exec_driver_sql('CREATE TABLE franchises (id INTEGER PRIMARY KEY,business_name VARCHAR,franchise_code VARCHAR,is_performance_active BOOLEAN)')
        connection.exec_driver_sql('CREATE TABLE roles (id INTEGER PRIMARY KEY,name VARCHAR)')
        connection.exec_driver_sql('CREATE TABLE user_roles (user_id INTEGER,role_id INTEGER)')
        connection.exec_driver_sql('CREATE TABLE user_franchises (user_id INTEGER,franchise_id INTEGER,is_primary BOOLEAN)')
        connection.exec_driver_sql('CREATE TABLE franchise_registrations (id INTEGER PRIMARY KEY,email VARCHAR NOT NULL UNIQUE,business_name VARCHAR NOT NULL)')
        connection.exec_driver_sql("INSERT INTO roles VALUES (1,'Admin'),(2,'Finance Assistant'),(3,'Franchise User')")
        connection.exec_driver_sql("INSERT INTO users VALUES (1,'wjm@martinsdirect.com','admin-hash',true,true),(170,'deon@martinsdirect.com','finance-hash',true,true),(29,'krugersdorp@martinsdirect.com','owner-hash',true,true),(35,'northcliff@martinsdirect.com','northcliff-hash',true,true),(6,'benoni@martinsdirect.com','benoni-hash',true,true),(13,'daveyton@martinsdirect.com','daveyton-hash',true,true)")
        connection.exec_driver_sql("INSERT INTO franchises VALUES (29,'Krugersdorp (F)','KRUGERSDORP',true),(35,'Northcliff (F)','NORTHCLIFF',true),(6,'Benoni (F)','BENONI',true),(13,'Daveyton (F)','DAVEYTON',true)")
        connection.exec_driver_sql('INSERT INTO user_roles VALUES (1,1),(170,2),(29,3),(35,3),(6,3),(13,3)')
        connection.exec_driver_sql('INSERT INTO user_franchises VALUES (29,29,true),(35,35,true),(6,6,true),(13,13,true),(6,13,false)')
        before = {table: connection.exec_driver_sql(f'SELECT * FROM {table}').all() for table in ('users','user_roles','user_franchises')}
        for name in ('v134_access_profiles.py','v135_deon_krugersdorp_profile.py','v136_branch_registration_profiles.py','v137_wjm_owner_profiles.py'):
            migration = load_migration(name)
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        assert connection.exec_driver_sql('SELECT identity_user_id,principal_user_id,franchise_id FROM user_access_profiles ORDER BY id').all() == [(170,29,29),(1,35,35),(1,13,13),(1,6,6)]
        for table, rows in before.items():
            assert connection.exec_driver_sql(f'SELECT * FROM {table}').all() == rows
        connection.exec_driver_sql("INSERT INTO franchise_registrations VALUES (1,'deon@martinsdirect.com','Krugersdorp'),(2,'deon@martinsdirect.com','Benoni')")
        with pytest.raises(RuntimeError):
            load = load_migration('v136_branch_registration_profiles.py')
            load.op = Operations(MigrationContext.configure(connection))
            load.downgrade()


@pytest.mark.parametrize('new_financial_data', [False, True])
def test_old_northcliff_removal_is_guarded_and_recoverable(new_financial_data):
    with create_engine('sqlite://').begin() as connection:
        connection.exec_driver_sql('CREATE TABLE franchises (id INTEGER PRIMARY KEY,business_name VARCHAR,franchise_code VARCHAR,is_performance_active BOOLEAN,minimum_royalty NUMERIC(12,2))')
        connection.exec_driver_sql('CREATE TABLE attendance_offices (id INTEGER PRIMARY KEY,franchise_id INTEGER REFERENCES franchises(id),name VARCHAR)')
        connection.exec_driver_sql('CREATE TABLE royalty_scales (id INTEGER PRIMARY KEY,franchise_id INTEGER REFERENCES franchises(id),percentage NUMERIC(12,2))')
        connection.exec_driver_sql('CREATE TABLE user_franchises (user_id INTEGER,franchise_id INTEGER REFERENCES franchises(id),is_primary BOOLEAN,PRIMARY KEY(user_id,franchise_id))')
        connection.exec_driver_sql('CREATE TABLE monthly_figures (id INTEGER PRIMARY KEY,franchise_id INTEGER REFERENCES franchises(id),amount NUMERIC(12,2))')
        connection.exec_driver_sql("INSERT INTO franchises VALUES (2,'MARTIN''S FUNERALS NORTHCLIFF','MF001',false,4000),(35,'Northcliff (F)','NORTHCLIFF',true,4500)")
        connection.exec_driver_sql("INSERT INTO attendance_offices VALUES (1,2,'Old office'),(2,35,'Active office')")
        connection.exec_driver_sql('INSERT INTO royalty_scales VALUES (1,2,6.5),(2,35,7)')
        connection.exec_driver_sql('INSERT INTO user_franchises VALUES (166,2,true),(35,35,true)')
        connection.exec_driver_sql('INSERT INTO monthly_figures VALUES (1,35,12345)')
        migration = load_migration('v138_retire_old_northcliff.py')
        migration.op = Operations(MigrationContext.configure(connection))
        if new_financial_data:
            connection.exec_driver_sql('INSERT INTO monthly_figures VALUES (2,2,1000)')
            with pytest.raises(RuntimeError, match='New data exists'):
                migration.upgrade()
            assert connection.exec_driver_sql('SELECT count(*) FROM franchises WHERE id=2').scalar() == 1
            assert connection.exec_driver_sql('SELECT amount FROM monthly_figures WHERE id=2').scalar() == 1000
        else:
            before = {table: connection.exec_driver_sql(f'SELECT * FROM {table}').all() for table in migration.TABLES}
            migration.upgrade()
            assert connection.exec_driver_sql('SELECT count(*) FROM franchises WHERE id=2').scalar() == 0
            assert connection.exec_driver_sql('SELECT franchise_code FROM franchises WHERE id=35').scalar() == 'NORTHCLIFF'
            assert connection.exec_driver_sql('SELECT amount FROM monthly_figures WHERE id=1').scalar() == 12345
            assert connection.exec_driver_sql('SELECT count(*) FROM franchise_retirement_backups').scalar() == 1
            migration.downgrade()
            for table, rows in before.items():
                assert {tuple(row) for row in connection.exec_driver_sql(f'SELECT * FROM {table}').all()} == {tuple(row) for row in rows}
