from datetime import timedelta
import re
import pytest
from flask import g
from test_native_territory import app, franchise, login
from app.extensions import db
from app.models import User, Role, UserModuleAccess, ModuleActivationCode, FranchiseRegistration, ImportJob, ensure_mandatory_franchise_modules
from app.module_licensing import now, start_trial, MODULES


def owner_client(app, franchise):
    client=login(app,franchise,role='Franchise User')
    user=User.query.first()
    user.email='holder@example.com'
    ensure_mandatory_franchise_modules(user)
    db.session.commit()
    return client,user


def token(client,path):
    g.pop('_login_user',None)
    assert client.get(path).status_code==200
    with client.session_transaction() as session:
        return session['registration_csrf']


def admin_client(app):
    admin=User(name='Head',surname='Office',email='admin@example.com',password_hash='x',roles=[Role(name='Admin')])
    db.session.add(admin)
    db.session.commit()
    client=app.test_client()
    with client.session_transaction() as s:
        s['_user_id']=str(admin.id)
        s['_fresh']=True
    return client


def issue(client,user,module='heat_map:view',confirmed='yes'):
    response = client.post('/admin/module-activations',data={'csrf_token':token(client,'/admin/module-activations'),
        'user_id':user.id,'module_code':module,'action':'issue','payment_confirmed':confirmed})
    return client.get(response.location) if confirmed == 'yes' and response.status_code == 302 else response


def test_trial_expiry_blocks_routes_keeps_manuals_and_cannot_restart(app,franchise):
    client,owner=owner_client(app,franchise)
    assert all(owner.has_permission(code) for code in MODULES)
    original=owner.module_trial_started_at
    for access in owner.module_access:
        if access.module_code in MODULES:
            access.trial_expires_at=now()-timedelta(seconds=1)
    db.session.commit()
    start_trial(owner)
    assert owner.module_trial_started_at==original
    assert not any(owner.has_permission(code) for code in MODULES)
    assert owner.has_permission('manuals:view')
    for path in ['/launch/attendance','/launch/insurance','/heat-map/']:
        assert client.get(path).status_code==403
    response=client.get('/modules')
    assert response.status_code==200
    assert 'href="/launch/attendance"' not in response.text
    assert 'Permanently available' in response.text


def test_paid_code_single_use_bound_to_holder_and_module_and_deactivatable(app,franchise):
    client,owner=owner_client(app,franchise)
    admin=admin_client(app)
    assert issue(admin,owner,confirmed='').status_code==302
    assert ModuleActivationCode.query.count()==0
    response=issue(admin,owner)
    value=re.search(r'[A-F0-9]{8}(?:-[A-F0-9]{8}){3}',response.text).group()
    saved=ModuleActivationCode.query.one()
    assert saved.code_hash!=value and saved.redeemed_at is None
    other=User(name='Other',surname='Owner',email='other@example.com',password_hash='x',roles=[owner.roles[0]])
    db.session.add(other)
    db.session.commit()
    wrong=app.test_client()
    with wrong.session_transaction() as s:
        s['_user_id']=str(other.id)
    wrong.post('/modules',data={'csrf_token':token(wrong,'/modules'),'activation_code':value})
    assert saved.redeemed_at is None
    response=client.post('/modules',data={'csrf_token':token(client,'/modules'),'activation_code':value})
    assert response.status_code==302 and saved.redeemed_at
    access=UserModuleAccess.query.filter_by(user_id=owner.id,module_code='heat_map:view').one()
    access.trial_expires_at=now()-timedelta(days=90)
    db.session.commit()
    assert owner.has_permission('heat_map:view')
    activated=access.paid_activated_at
    client.post('/modules',data={'csrf_token':token(client,'/modules'),'activation_code':value})
    assert access.paid_activated_at==activated
    admin.post('/admin/module-activations',data={'csrf_token':token(admin,'/admin/module-activations'),
        'action':'deactivate','user_id':owner.id,'module_code':'heat_map:view'})
    assert not owner.has_permission('heat_map:view')
    client.post('/modules',data={'csrf_token':token(client,'/modules'),'activation_code':value})
    assert not owner.has_permission('heat_map:view')


def test_unused_codes_cancelled_on_deactivation_and_non_admin_cannot_issue(app,franchise):
    client,owner=owner_client(app,franchise)
    assert client.get('/admin/module-activations').status_code==403
    assert client.post('/admin/module-activations').status_code==403
    admin=admin_client(app)
    issue(admin,owner)
    admin.post('/admin/module-activations',data={'csrf_token':token(admin,'/admin/module-activations'),
        'action':'deactivate','user_id':owner.id,'module_code':'heat_map:view'})
    assert ModuleActivationCode.query.one().revoked_at
    assert admin.post('/admin/module-activations',data={}).status_code==400


def test_employee_permissions_cannot_bypass_expired_owner_license(app,franchise):
    client,owner=owner_client(app,franchise)
    from app.models import Permission
    role=Role(name='Franchise Employee',permissions=[Permission(module='Attendance',action='view',code='attendance:view',label='Attendance')])
    staff=User(name='Staff',surname='Member',email='staff@example.com',password_hash='x',roles=[role],parent_franchise_user_id=owner.id)
    db.session.add(staff)
    db.session.commit()
    assert staff.has_permission('attendance:view')
    UserModuleAccess.query.filter_by(user_id=owner.id,module_code='attendance:view').one().trial_expires_at=now()-timedelta(seconds=1)
    db.session.commit()
    assert not staff.has_permission('attendance:view')


def test_external_entitlement_endpoint_requires_signature_and_honors_expiry(app,franchise,monkeypatch):
    _,owner=owner_client(app,franchise)
    from itsdangerous import URLSafeTimedSerializer
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET','test-shared-secret')
    signed=URLSafeTimedSerializer('test-shared-secret',salt='martins-module-access-v1').dumps({'email':owner.email,'module':'attendance:view'})
    client=app.test_client()
    assert client.post('/api/module-access',json={'module':'attendance:view','token':'fake'}).status_code==403
    assert client.post('/api/module-access',json={'module':'attendance:view','token':signed}).json['active']
    UserModuleAccess.query.filter_by(user_id=owner.id,module_code='attendance:view').one().is_enabled=False
    db.session.commit()
    assert not client.post('/api/module-access',json={'module':'attendance:view','token':signed}).json['active']


def test_head_office_alert_queue_recipient_retry_and_idempotence(app,monkeypatch):
    from app.registration_alerts import queue_alert,send_alert
    application=FranchiseRegistration(name='New',surname='Holder',business_name='Branch',phone='0821234567',email='applicant@example.com',password_hash='hashed')
    db.session.add(application)
    db.session.flush()
    queue_alert(application)
    db.session.commit()
    job=ImportJob.query.one()
    assert job.queue_name=='territory' and job.max_attempts==3
    monkeypatch.setattr('app.registration.notify',lambda *args:False)
    with pytest.raises(RuntimeError):
        send_alert(job)
    assert application.admin_alert_sent_at is None
    sent=[]
    monkeypatch.setattr('app.registration.notify',lambda *args:sent.append(args) or True)
    send_alert(job)
    assert sent[0][3]=='wjm@martinsdirect.com'
    assert 'hashed' not in sent[0][2]
    send_alert(job)
    assert len(sent)==1


def test_migration_backfills_one_trial_and_preserves_head_office():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine,inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('license_migration',Path(__file__).resolve().parents[1]/'migrations/versions/v131_module_licensing.py')
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with create_engine('sqlite:///:memory:').begin() as connection:
        for sql in ['CREATE TABLE users (id INTEGER PRIMARY KEY)', 'CREATE TABLE roles (id INTEGER PRIMARY KEY,name TEXT)',
                    'CREATE TABLE user_roles (user_id INTEGER,role_id INTEGER)',
                    'CREATE TABLE user_module_access (id INTEGER PRIMARY KEY,user_id INTEGER,module_code TEXT,is_enabled BOOLEAN,updated_at DATETIME)',
                    'CREATE TABLE franchise_registrations (id INTEGER PRIMARY KEY)',
                    'INSERT INTO users VALUES (1),(2)', "INSERT INTO roles VALUES (1,'Franchise User'),(2,'Admin')",
                    'INSERT INTO user_roles VALUES (1,1),(2,1),(2,2)']:
            connection.exec_driver_sql(sql)
        migration.op=Operations(MigrationContext.configure(connection))
        migration.upgrade()
        rows=connection.exec_driver_sql('SELECT user_id,trial_expires_at,is_enabled FROM user_module_access').all()
        assert len(rows)==3 and all(row[0]==1 and row[1] and row[2] for row in rows)
        assert connection.exec_driver_sql('SELECT module_trial_started_at FROM users WHERE id=2').scalar() is None
        migration.downgrade()
        assert 'module_activation_codes' not in inspect(connection).get_table_names()
