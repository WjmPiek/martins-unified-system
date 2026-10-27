import importlib.util
from pathlib import Path
import re

from flask import g
from sqlalchemy import create_engine, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_native_territory import app, franchise
from test_module_licensing import owner_client, admin_client, issue, token
from test_access_profiles import setup_accounts, sign_in, call
from app.extensions import db
from app.models import ModuleActivationCode, ModuleSubscriptionRequest, UserModuleAccess, User
from app.activation_delivery import reveal, registered_recipients
from app.module_licensing import now


def test_code_saved_encrypted_admin_only_reissue_cancels_previous(app, franchise):
    holder, owner = owner_client(app, franchise)
    admin = admin_client(app)
    response = issue(admin, owner)
    saved = ModuleActivationCode.query.one()
    value = reveal(saved)
    assert value and value in response.text
    assert value not in saved.encrypted_code and value != saved.code_hash
    assert value in admin.get('/admin/module-activations').text
    g.pop('_login_user', None)
    assert value not in holder.get('/modules').text
    assert holder.get('/admin/module-activations').status_code == 403
    issue(admin, owner)
    assert saved.revoked_at
    assert ModuleActivationCode.query.count() == 2
    assert reveal(ModuleActivationCode.query.order_by(ModuleActivationCode.id.desc()).first()) != value
    app.config['SECRET_KEY'] = 'rotated-key'
    assert reveal(saved) is None


def test_successful_email_and_failure_retry_keep_same_code(app, franchise, monkeypatch):
    _, owner = owner_client(app, franchise)
    admin = admin_client(app)
    sent = []
    class Connection:
        fail = True
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, message):
            if self.fail: raise RuntimeError('sensitive mail error')
            sent.append(message)
    monkeypatch.setattr('app.registration.RegistrationMailConnection', Connection)
    response = issue(admin, owner)
    saved = ModuleActivationCode.query.one()
    value = reveal(saved)
    assert saved.email_attempts == 1 and saved.email_last_error and not saved.email_sent_at
    assert 'sensitive mail error' not in response.text and 'Retry email' in response.text
    Connection.fail = False
    admin.post('/admin/module-activations', data=dict(csrf_token=token(admin, '/admin/module-activations'),
        action='send_email', user_id=owner.id, module_code=saved.module_code, code_id=saved.id))
    assert saved.email_attempts == 2 and saved.email_sent_at and not saved.email_last_error
    assert len(sent) == 1 and sent[0].recipients == [owner.email]
    assert value in sent[0].body and 'Activated Modules > Subscriptions' in sent[0].body
    assert 'https://martinssystem.co.za' in sent[0].body
    assert ModuleActivationCode.query.count() == 1 and reveal(saved) == value
    saved.revoked_at = now()
    db.session.commit()
    admin.post('/admin/module-activations', data=dict(csrf_token=token(admin, '/admin/module-activations'),
        action='send_email', user_id=owner.id, module_code=saved.module_code, code_id=saved.id))
    assert len(sent) == 1


def test_registered_identity_preferred_and_requester_recorded(app, monkeypatch):
    identity, owner, branches, profile = setup_accounts(app)
    client = app.test_client()
    sign_in(client, f'branch:{profile.id}')
    call(client, 'get', '/modules')
    with client.session_transaction() as session: csrf = session['registration_csrf']
    response = call(client, 'post', '/modules/request', data=dict(csrf_token=csrf, module_codes=['heat_map:view']))
    assert response.status_code == 302
    subscription = ModuleSubscriptionRequest.query.one()
    assert subscription.requested_by_user_id == identity.id and subscription.owner_user_id == owner.id
    options = registered_recipients(owner, subscription.items[0])
    assert next(item for item in options if item['preferred'])['email'] == identity.email
    admin = admin_client(app)
    issue(admin, owner)
    saved = ModuleActivationCode.query.one()
    assert saved.recipient_email == identity.email
    page = admin.get('/admin/module-activations?q=deon%40martinsdirect.com')
    assert f'id="holder-{owner.id}"' in page.text
    profile.is_active = False
    db.session.commit()
    from app.activation_delivery import send_code_email
    assert not send_code_email(saved, owner)


def test_arbitrary_email_rejected_and_search_by_branch_or_literal_wildcard(app, franchise):
    _, owner = owner_client(app, franchise)
    admin = admin_client(app)
    admin.post('/admin/module-activations', data=dict(csrf_token=token(admin, '/admin/module-activations'),
        action='issue', user_id=owner.id, module_code='heat_map:view', payment_confirmed='yes', recipient_email='stranger@example.com'))
    assert ModuleActivationCode.query.count() == 0
    for q in ['Test franchise', 'holder@example.com']:
        assert f'id="holder-{owner.id}"' in admin.get('/admin/module-activations', query_string={'q':q}).text
    assert f'id="holder-{owner.id}"' not in admin.get('/admin/module-activations?q=%25').text
    page = admin.get('/admin/module-activations', query_string={'q':'<script>alert(1)</script>'})
    assert '<script>alert(1)</script>' not in page.text


def test_subscription_available_choices_one_all_and_deactivation(app, franchise):
    holder, owner = owner_client(app, franchise)
    access = UserModuleAccess.query.filter_by(user_id=owner.id, module_code='heat_map:view').one()
    access.paid_activated_at = now()
    db.session.commit()
    page = holder.get('/modules').text
    assert 'Active subscriptions' in page and 'Select Attendance' in page
    assert 'name="module_codes" value="heat_map:view"' not in page
    UserModuleAccess.query.filter_by(user_id=owner.id, module_code='attendance:view').one().paid_activated_at = now()
    db.session.commit()
    page = holder.get('/modules').text
    assert 'All optional modules are active' in page and 'Subscriptions</' in page
    assert 'id="module-selection"' not in page and 'Request Selected Modules' not in page
    assert 'Permanently available' in page
    access.is_enabled = False
    db.session.commit()
    page = holder.get('/modules').text
    assert 'Select Heat Map' in page and 'All optional modules are active' not in page


def test_delivery_migration_preserves_legacy_hash_and_requests():
    spec = importlib.util.spec_from_file_location('delivery_migration', Path(__file__).parents[1]/'migrations/versions/v139_activation_code_delivery.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE module_subscription_requests (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE module_activation_codes (id INTEGER PRIMARY KEY, code_hash TEXT)')
        connection.exec_driver_sql("INSERT INTO module_activation_codes VALUES (7, 'existing-hash')")
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.exec_driver_sql('SELECT code_hash, encrypted_code, email_attempts FROM module_activation_codes').one() == ('existing-hash', None, 0)
        assert 'requested_by_user_id' in {c['name'] for c in inspect(connection).get_columns('module_subscription_requests')}
        migration.downgrade()
        assert connection.exec_driver_sql('SELECT code_hash FROM module_activation_codes').scalar() == 'existing-hash'
