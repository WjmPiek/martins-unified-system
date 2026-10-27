from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import re
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import g
from flask_login import AnonymousUserMixin
from test_native_territory import app, franchise
from test_module_licensing import owner_client, admin_client, token, issue
from test_module_catalog import select
from app.extensions import db
from app.models import HeatmapRecord, UserModuleAccess, ImportJob, ModuleSubscriptionRequest, User
from app.module_reminders import trial_warning
from app.module_alerts import send_subscription_alert


@pytest.mark.parametrize('remaining,days',[(6*86400,None),(5*86400,5),(4*86400+1,5),(86400,1),(1,1),(0,None),(-1,None)])
def test_warning_window_and_day_rounding(app,franchise,monkeypatch,remaining,days):
    client,owner=owner_client(app,franchise)
    clock=datetime(2026,10,27,23,0,tzinfo=timezone.utc)
    monkeypatch.setattr('app.module_reminders.now',lambda:clock)
    for access in owner.module_access:
        if access.module_code in ('heat_map:view','attendance:view'):
            access.trial_expires_at=(clock+timedelta(seconds=remaining)).replace(tzinfo=None)
    result=trial_warning(owner)
    if days is None:
        assert result is None
    else:
        assert len(result['items'])==2 and all(item['days']==days for item in result['items'])
        assert '2026-10-28' in result['dismiss_key']


def test_global_popup_action_and_paid_disabled_paused_exclusions(app,franchise):
    from app.module_licensing import now
    client,owner=owner_client(app,franchise)
    for access in owner.module_access:
        access.trial_expires_at=now()+timedelta(days=4,hours=23)
    db.session.add(UserModuleAccess(user_id=owner.id,module_code='insurance_app:view',is_enabled=True,trial_expires_at=now()+timedelta(days=4)))
    db.session.commit()
    response=client.get('/manuals/')
    assert response.status_code==200
    assert 'id="module-trial-warning"' in response.text
    assert 'expires in 5 days' in response.text
    assert 'data-trial-subscribe' in response.text and 'Request Subscription' in response.text
    assert 'localStorage.setItem' in response.text and 'Insurance Applications' not in response.text
    for access in owner.module_access:
        if access.module_code=='heat_map:view':
            access.paid_activated_at=now()
        elif access.module_code=='attendance:view':
            access.is_enabled=False
    db.session.commit()
    assert trial_warning(owner) is None
    assert 'id="module-trial-warning"' not in client.get('/modules').text
    assert trial_warning(AnonymousUserMixin()) is None
    admin=admin_client(app)
    g.pop('_login_user',None)
    assert 'id="module-trial-warning"' not in admin.get('/admin/module-activations').text


def test_subscription_alert_atomic_queue_retry_contents_and_no_duplicate_send(app,franchise,monkeypatch):
    client,owner=owner_client(app,franchise)
    select(client,['heat_map:view','attendance:view'])
    application=ModuleSubscriptionRequest.query.one()
    job=ImportJob.query.filter_by(kind='module_subscription_head_office_alert').one()
    assert job.queue_name=='territory' and job.max_attempts==3
    assert str(application.id) in job.payload_json
    select(client,['heat_map:view'])
    assert ImportJob.query.filter_by(kind='module_subscription_head_office_alert').count()==1
    monkeypatch.setattr('app.registration.notify',lambda *args:False)
    with pytest.raises(RuntimeError):
        send_subscription_alert(job)
    assert application.admin_alert_sent_at is None
    sent=[]
    monkeypatch.setattr('app.registration.notify',lambda *args:sent.append(args) or True)
    send_subscription_alert(job)
    assert application.admin_alert_sent_at and job.status=='completed'
    assert sent[0][3]=='wjm@martinsdirect.com'
    for value in [owner.email,owner.full_name,franchise.business_name,'Heat Map','Attendance','R80.38','/admin/module-activations']:
        assert value in sent[0][2]
    assert 'password' not in sent[0][2].lower()
    send_subscription_alert(job)
    assert len(sent)==1


def test_module_data_survives_trial_expiry_and_paid_reactivation(app,franchise):
    from app.module_licensing import now
    client,owner=owner_client(app,franchise)
    record=HeatmapRecord(franchise_id=franchise.id,mf_file='KEEP-001',deceased_name='Saved',deceased_surname='Client',city='Germiston',province='Gauteng',relation='MAP:insurance_clients',source_filename='retained.xlsx')
    db.session.add(record)
    for access in owner.module_access:
        access.trial_expires_at=now()-timedelta(seconds=1)
    db.session.commit()
    saved_id=record.id
    assert client.get('/heat-map/').status_code==403
    assert db.session.get(HeatmapRecord,saved_id).mf_file=='KEEP-001'
    admin=admin_client(app)
    response=issue(admin,owner)
    code=re.search(r'[A-F0-9]{8}(?:-[A-F0-9]{8}){3}',response.text).group()
    client.post('/modules',data={'csrf_token':token(client,'/modules'),'activation_code':code})
    assert client.get('/heat-map/').status_code==200
    assert HeatmapRecord.query.count()==1 and db.session.get(HeatmapRecord,saved_id).city=='Germiston'


def test_alert_migration_preserves_existing_subscription_rows():
    spec=importlib.util.spec_from_file_location('subscription_alert_migration',Path(__file__).parents[1]/'migrations/versions/v133_module_subscription_alerts.py')
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sa.create_engine('sqlite:///:memory:').begin() as connection:
        connection.exec_driver_sql('CREATE TABLE module_subscription_requests (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('INSERT INTO module_subscription_requests VALUES (1)')
        migration.op=Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.exec_driver_sql('SELECT admin_alert_sent_at FROM module_subscription_requests WHERE id=1').scalar() is None
        migration.downgrade()
        assert connection.exec_driver_sql('SELECT id FROM module_subscription_requests').scalar()==1
