import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import g
from itsdangerous import URLSafeTimedSerializer

from test_native_territory import app
from app.extensions import db
from app.models import Franchise, Role, User
from app.account_identity import can_share_owner_email


def owners():
    role = Role(name='Franchise User')
    branches = [Franchise(business_name=name) for name in ('Krugersdorp', 'Mogale City')]
    users = [User(name='Owner', surname=str(i), email='shared@example.com', roles=[role],
                  assigned_franchises=[branch]) for i, branch in enumerate(branches)]
    for user in users:
        user.set_password('shared-password')
    db.session.add_all(users)
    db.session.commit()
    return users, branches


def start(client, password='shared-password'):
    g.pop('_login_user', None)
    return client.post('/login', data={'email': ' SHARED@example.com ', 'password': password})


def choose(client, user_id, **changes):
    with client.session_transaction() as session:
        token = session.get('login_accounts', {}).get('token', '')
    data = dict(account_id=user_id, choice_token=token)
    data.update(changes)
    g.pop('_login_user', None)
    return client.post('/login', data=data)


def test_same_email_requires_choice_and_preserves_franchise_isolation(app):
    users, branches = owners()
    client = app.test_client()
    response = start(client)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert all(branch.business_name in response.text for branch in branches)
    with client.session_transaction() as session:
        assert '_user_id' not in session
        session['selected_franchise_id'] = branches[0].id
        session['franchise_view_mode'] = True
    assert choose(client, users[1].id).status_code == 302
    with client.session_transaction() as session:
        assert session['_user_id'] == str(users[1].id)
        assert 'selected_franchise_id' not in session
        assert 'login_accounts' not in session
    assert users[1].can_access_franchise(branches[1].id)
    assert not users[1].can_access_franchise(branches[0].id)


@pytest.mark.parametrize('attack', ['forged', 'expired', 'inactive', 'password_changed', 'no_proof'])
def test_choice_rejects_invalid_or_stale_authentication(app, attack):
    users, _ = owners()
    client = app.test_client()
    if attack != 'no_proof':
        start(client)
    if attack == 'expired':
        with client.session_transaction() as session:
            pending = session['login_accounts']
            pending['created'] = 0
            session['login_accounts'] = pending
    if attack == 'inactive':
        users[1].is_active_account = False
        db.session.commit()
    if attack == 'password_changed':
        users[1].set_password('changed-password')
        db.session.commit()
    choose(client, users[1].id, **({'choice_token': 'forged'} if attack == 'forged' else {}))
    with client.session_transaction() as session:
        assert '_user_id' not in session


def test_different_password_requires_proof_for_selected_account(app):
    users, _ = owners()
    users[1].set_password('other-password')
    db.session.commit()
    client = app.test_client()
    assert start(client).status_code == 200
    choose(client, users[1].id)
    with client.session_transaction() as session:
        assert '_user_id' not in session
    start(client)
    assert choose(client, users[1].id, password='other-password').status_code == 302
    with client.session_transaction() as session:
        assert session['_user_id'] == str(users[1].id)


def test_inactive_accounts_hidden_and_bad_password_never_reveals_choices(app):
    users, branches = owners()
    client = app.test_client()
    response = start(client, 'wrong-password')
    assert all(branch.business_name not in response.text for branch in branches)
    users[1].is_active = False
    db.session.commit()
    assert start(client).status_code == 302
    with client.session_transaction() as session:
        assert session['_user_id'] == str(users[0].id)


def test_reset_links_target_account_ids_and_ambiguous_legacy_links_fail(app, monkeypatch):
    users, _ = owners()
    tokens = [user.get_reset_token() for user in users]
    assert [User.verify_reset_token(token).id for token in tokens] == [u.id for u in users]
    legacy = URLSafeTimedSerializer(app.secret_key).dumps(users[0].email, salt='password-reset-salt')
    assert User.verify_reset_token(legacy) is None
    sent = []
    monkeypatch.setattr('app.auth.routes.send_password_reset_email', lambda user: (sent.append(user.id) or True, None))
    app.test_client().post('/forgot-password', data={'email': users[0].email})
    assert sent == [user.id for user in users]
    app.test_client().post('/reset-password/' + tokens[1], data={'password': 'new-password', 'confirm_password': 'new-password'})
    assert users[1].check_password('new-password')
    assert users[0].check_password('shared-password')


def test_only_distinct_franchise_owners_can_share_email(app):
    users, branches = owners()
    assert can_share_owner_email('shared@example.com')
    assert not can_share_owner_email('shared@example.com', branches[0].id)
    assert not can_share_owner_email('wjm@martinsdirect.com')
    users[1].roles = [Role(name='Finance Manager')]
    db.session.commit()
    assert not can_share_owner_email('shared@example.com')


def test_admin_can_create_a_second_franchise_with_same_owner_email(app):
    users, branches = owners()
    admin = User(name='Admin', surname='User', email='admin@example.com', password_hash='x', roles=[Role(name='Admin')])
    db.session.add(admin)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin.id)
        session['_fresh'] = True
    response = client.post('/franchise/details/new-franchise-user', data={
        'business_name': 'Third branch', 'user_name': 'Owner', 'user_surname': 'Third',
        'user_email': 'shared@example.com', 'password': 'third-password'})
    assert response.status_code == 302
    created = User.query.filter_by(email='shared@example.com').order_by(User.id.desc()).first()
    assert User.query.filter_by(email='shared@example.com').count() == 3
    assert created.accessible_franchises()[0].business_name == 'Third branch'
    assert all(not created.can_access_franchise(branch.id) for branch in branches)


def test_master_import_keeps_shared_email_owners_separate(app):
    from app.franchise_master_data import _ensure_franchise_user_link
    users, branches = owners()
    row = {'Login User Email': 'shared@example.com'}
    assert _ensure_franchise_user_link(branches[1], row) == (0, 0)
    assert users[0].assigned_franchises == [branches[0]]
    third = Franchise(business_name='Third branch')
    db.session.add(third)
    db.session.flush()
    assert _ensure_franchise_user_link(third, row) == (1, 1)
    assert User.query.filter_by(email='shared@example.com').count() == 3
    with pytest.raises(ValueError, match='Head Office'):
        _ensure_franchise_user_link(third, {'Login User Email': 'wjm@martinsdirect.com'})


def test_shared_email_registration_still_requires_approval(app, monkeypatch):
    from test_registration_flow import apply, review
    from test_native_territory import login
    from app.models import FranchiseRegistration
    users, branches = owners()
    monkeypatch.setattr('app.registration.notify', lambda *args: False)
    client = app.test_client()
    assert apply(client, email='shared@example.com', business_name='Third branch').status_code == 302
    application = FranchiseRegistration.query.one()
    assert application.status == 'pending'
    assert User.query.filter_by(email='shared@example.com').count() == 2
    admin = login(app, branches[0])
    assert review(admin, application).status_code == 302
    assert application.status == 'approved'
    assert User.query.filter_by(email='shared@example.com').count() == 3


def test_external_entitlements_require_unambiguous_account_identity(app, monkeypatch):
    users, _ = owners()
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET', 'test-launch-secret')
    serializer = URLSafeTimedSerializer('test-launch-secret', salt='martins-module-access-v1')
    client = app.test_client()
    data = {'email': 'shared@example.com', 'module': 'attendance:view'}
    response = client.post('/api/module-access', json={'module': 'attendance:view', 'token': serializer.dumps(data)})
    assert response.status_code == 200 and response.json['active'] is False
    response = client.post('/api/module-access', json={'module': 'attendance:view', 'token': serializer.dumps(dict(data, user_id=users[1].id))})
    assert response.status_code == 200 and response.json['managed'] is True


def test_migration_preserves_rows_and_allows_shared_email():
    path = Path(__file__).parents[1] / 'migrations/versions/v134_shared_franchise_email.py'
    spec = importlib.util.spec_from_file_location('shared_email_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL)')
        connection.exec_driver_sql('CREATE UNIQUE INDEX ix_users_email ON users (email)')
        connection.exec_driver_sql('CREATE TABLE franchise_registrations (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL UNIQUE, business_name VARCHAR NOT NULL)')
        connection.exec_driver_sql("INSERT INTO users VALUES (1, 'owner@example.com')")
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.exec_driver_sql("INSERT INTO users VALUES (2, 'owner@example.com')")
        connection.exec_driver_sql("INSERT INTO franchise_registrations VALUES (1, 'owner@example.com', 'Branch A'), (2, 'owner@example.com', 'Branch B')")
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM users').scalar() == 2
        with pytest.raises(RuntimeError, match='Resolve shared emails'):
            migration.downgrade()
