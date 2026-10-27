import pytest
from test_native_territory import app, franchise, login
from app.extensions import db
from app.models import User, Franchise, FranchiseRegistration
from flask import g
from app.registration import notify as real_notify


@pytest.fixture(autouse=True)
def no_email(monkeypatch):
    monkeypatch.setattr('app.registration.notify', lambda *args: False)


def apply(client, **changes):
    client.get('/register')
    with client.session_transaction() as s:
        token = s['registration_csrf']
    data = dict(name='New', surname='Holder', email='new@example.com', business_name='New Branch',
                phone='0821234567', password='Chosen-password1', confirm_password='Chosen-password1',
                confirm_application='yes', csrf_token=token)
    data.update(changes)
    response = client.post('/register', data=data)
    g.pop('_login_user', None)  # The shared test fixture retains its app context.
    return response


def review(client, application, **changes):
    g.pop('_login_user', None)
    assert client.get('/admin/registrations').status_code == 200
    with client.session_transaction() as s:
        token = s['registration_csrf']
    data = dict(action='approve', franchise_id='new', agreement_start_date='2026-01-01',
                agreement_end_date='2030-01-01', review_confirmed='yes', csrf_token=token)
    data.update(changes)
    return client.post(f'/admin/registrations/{application.id}/review', data=data)


def test_first_registration_has_no_user_franchise_or_access_and_prevents_duplicates(app):
    client = app.test_client()
    assert apply(client).location.endswith('/registration/received')
    assert User.query.count() == Franchise.query.count() == 0
    application = FranchiseRegistration.query.one()
    assert application.status == 'pending'
    assert application.password_hash != 'Chosen-password1'
    client.post('/login', data={'email': application.email, 'password': 'Chosen-password1'})
    with client.session_transaction() as s:
        assert '_user_id' not in s
    assert client.get('/admin/registrations').status_code == 302
    assert apply(client).status_code == 200
    assert FranchiseRegistration.query.count() == 1


def test_approve_new_creates_scoped_login_once_and_keeps_chosen_password(app, franchise):
    public = app.test_client()
    apply(public)
    admin = login(app, franchise)
    application = FranchiseRegistration.query.one()
    assert review(admin, application).status_code == 302
    user = db.session.get(User, application.user_id)
    assert application.status == 'approved' and application.password_hash == ''
    assert user.check_password('Chosen-password1')
    assert [r.name for r in user.roles] == ['Franchise User']
    assert len(user.accessible_franchises()) == 1
    assert user.accessible_franchises()[0].business_name == 'New Branch'
    assert not user.can_access_franchise(franchise.id)
    g.pop('_login_user', None)
    assert public.post('/login', data={'email': user.email, 'password': 'Chosen-password1'}).status_code == 302
    review(admin, application)
    assert User.query.filter_by(email='new@example.com').count() == 1


def test_approve_existing_franchise_does_not_duplicate_record(app, franchise):
    apply(app.test_client())
    admin = login(app, franchise)
    application = FranchiseRegistration.query.one()
    review(admin, application, franchise_id=str(franchise.id))
    assert db.session.get(User, application.user_id).assigned_franchise_id() == franchise.id
    assert Franchise.query.count() == 1


def test_non_admin_cannot_review_and_rejected_application_has_no_access(app, franchise):
    apply(app.test_client())
    client = login(app, franchise, role='Franchise User')
    application = FranchiseRegistration.query.one()
    assert client.get('/admin/registrations').status_code == 403
    assert client.post(f'/admin/registrations/{application.id}/review').status_code == 403
    User.query.first().roles[0].name = 'Admin'
    db.session.commit()
    review(client, application, action='reject')
    assert application.status == 'rejected' and application.user_id is None
    assert application.password_hash == '' and User.query.count() == 1


@pytest.mark.parametrize('changes', [{'email':'bad'}, {'password':'short'}, {'confirm_password':'wrong'}, {'confirm_application':''}])
def test_validation_never_creates_account(app, changes):
    client = app.test_client()
    assert apply(client, **changes).status_code == 200
    assert User.query.count() == FranchiseRegistration.query.count() == 0


def test_csrf_required_and_name_cannot_grant_admin_protection(app):
    assert app.test_client().post('/register', data={}).status_code == 400
    user = User(name='Wjm', surname='Piek', email='applicant@example.com', password_hash='x')
    assert not user.is_protected_admin


def test_registration_migration_upgrade_and_downgrade():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/v130_franchise_registration.py'
    spec = importlib.util.spec_from_file_location('registration_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with create_engine('sqlite:///:memory:').begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert 'franchise_registrations' in inspect(connection).get_table_names()
        migration.downgrade()
        assert 'franchise_registrations' not in inspect(connection).get_table_names()


def test_notification_success_and_transport_failure_are_bounded(app, monkeypatch):
    from app.extensions import mail
    application = FranchiseRegistration(id=12, email='holder@example.com')
    app.config['MAIL_DEFAULT_SENDER'] = 'office@example.com'
    app.extensions['mail'].default_sender = 'office@example.com'
    with mail.record_messages() as outbox:
        assert real_notify(application, 'Registration update', 'Approved. Use your chosen password.')
        assert outbox[0].recipients == ['holder@example.com']
    state = app.extensions['mail']
    state.suppress = False
    def fail(*args, **kwargs):
        assert kwargs['timeout'] == 5
        raise TimeoutError()
    monkeypatch.setattr('app.registration.smtplib.SMTP', fail)
    assert real_notify(application, 'Registration update', 'Body') is False


def test_existing_owner_and_invalid_franchise_block_approval(app, franchise):
    apply(app.test_client())
    client = login(app, franchise, role='Franchise User')
    # Add an administrator alongside the existing owner.
    from app.models import Role
    admin_role = Role(name='Admin')
    admin = User(name='Head', surname='Office', email='admin@example.com', password_hash='x', roles=[admin_role])
    db.session.add(admin)
    db.session.commit()
    with client.session_transaction() as s:
        s['_user_id'] = str(admin.id)
    application = FranchiseRegistration.query.one()
    review(client, application, franchise_id='bad')
    assert application.status == 'pending'
    review(client, application, franchise_id=str(franchise.id))
    assert application.status == 'pending' and application.user_id is None
    review(client, application, agreement_end_date='2020-01-01')
    assert application.status == 'pending' and Franchise.query.count() == 1
