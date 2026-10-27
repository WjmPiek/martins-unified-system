from decimal import Decimal
import importlib.util
from pathlib import Path
import re
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import g
from test_native_territory import app, franchise, login
from test_module_licensing import owner_client, admin_client, token, issue
from app.extensions import db
from app.models import ModulePlan, ModuleSubscriptionRequest, ModuleSubscriptionItem, ModuleActivationCode, User, UserModuleAccess


def select(client, codes, **extra):
    return client.post('/modules/request', data={'csrf_token':token(client,'/modules'), 'module_codes':codes, **extra})


def get(client,path):
    g.pop('_login_user',None)
    return client.get(path)


def price(client, value):
    return client.post('/admin/module-activations',data={'csrf_token':token(client,'/admin/module-activations'),
        'action':'set_price','module_code':'heat_map:view','monthly_premium':value})


def test_cards_multiple_modules_server_price_main_owner_and_duplicate_prevention(app,franchise):
    client,owner=owner_client(app,franchise)
    response=client.get('/modules')
    assert response.status_code==200
    assert response.text.count('data-cents="4019"')==2
    assert 'Insurance Applications' not in response.text
    assert 'R40.19' in response.text and 'Permanently available' in response.text
    assert 'client locations' in response.text and franchise.business_name in response.text
    with client.session_transaction() as s:
        s['selected_franchise_id']=9999
    assert select(client,['heat_map:view','attendance:view'],owner_user_id=999,franchise_id=999,monthly_premium='0.01').status_code==302
    application=ModuleSubscriptionRequest.query.one()
    assert application.owner_user_id==owner.id and application.franchise_id==franchise.id
    assert application.monthly_total==Decimal('80.38') and len(application.items)==2
    assert select(client,['heat_map:view']).status_code==302
    assert ModuleSubscriptionRequest.query.count()==1
    admin=admin_client(app)
    response=get(admin,'/admin/module-activations')
    assert owner.email in response.text and 'R80.38' in response.text
    assert 'name="monthly_premium"' not in response.text


def test_price_owner_authorization_and_quoted_premiums_retained(app,franchise):
    client,owner=owner_client(app,franchise)
    select(client,['heat_map:view'])
    admin=admin_client(app)
    assert price(admin,'65.50').status_code==403
    administrator=User.query.filter_by(email='admin@example.com').one()
    administrator.email='wjm@martinsdirect.com'
    db.session.commit()
    assert price(admin,'65.50').status_code==302
    assert db.session.get(ModulePlan,'heat_map:view').monthly_premium==Decimal('65.50')
    assert ModuleSubscriptionRequest.query.one().monthly_total==Decimal('40.19')
    assert 'R65.50' in get(client,'/modules').text
    other=User(name='Another',surname='Holder',email='another@example.com',password_hash='x',roles=owner.roles,assigned_franchises=[franchise])
    db.session.add(other)
    db.session.commit()
    separate=app.test_client()
    with separate.session_transaction() as s:
        s['_user_id']=str(other.id)
    assert 'Request #1' not in get(separate,'/modules').text
    select(separate,['heat_map:view'])
    assert ModuleSubscriptionRequest.query.filter_by(owner_user_id=other.id).one().monthly_total==Decimal('65.50')
    g.pop('_login_user',None)
    assert client.post('/admin/module-activations',data={'action':'set_price','monthly_premium':'1'}).status_code==403


@pytest.mark.parametrize('value',['-1','NaN','Infinity','1.234','10000000000',''])
def test_invalid_premium_cannot_be_saved(app,franchise,value):
    client=login(app,franchise)
    assert price(client,value).status_code==302
    assert db.session.get(ModulePlan,'heat_map:view').monthly_premium==Decimal('40.19')


@pytest.mark.parametrize('codes',[[],['manuals:view'],['unknown'],['insurance_app:view'],['heat_map:view','unknown']])
def test_invalid_module_selections_cannot_create_requests(app,franchise,codes):
    client,owner=owner_client(app,franchise)
    assert select(client,codes).status_code==302
    assert ModuleSubscriptionRequest.query.count()==0


def test_linked_code_request_lifecycle_and_deactivation_allows_new_request(app,franchise):
    client,owner=owner_client(app,franchise)
    select(client,['attendance:view'])
    item=ModuleSubscriptionItem.query.one()
    admin=admin_client(app)
    response=issue(admin,owner,module='attendance:view')
    code=re.search(r'[A-F0-9]{8}(?:-[A-F0-9]{8}){3}',response.text).group()
    assert item.status=='code_issued' and ModuleActivationCode.query.one().request_item_id==item.id
    client.post('/modules',data={'csrf_token':token(client,'/modules'),'activation_code':code})
    assert item.status=='active'
    assert select(client,['attendance:view']).status_code==302 and ModuleSubscriptionRequest.query.count()==1
    admin.post('/admin/module-activations',data={'csrf_token':token(admin,'/admin/module-activations'),
        'action':'deactivate','user_id':owner.id,'module_code':'attendance:view'})
    assert item.status=='deactivated'
    select(client,['attendance:view'])
    assert ModuleSubscriptionRequest.query.count()==2


def test_registration_requires_primary_holder_link_and_csrf(app,franchise):
    client,owner=owner_client(app,franchise)
    assert client.post('/modules/request',data={'module_codes':['heat_map:view']}).status_code==400
    owner.assigned_franchises=[]
    db.session.commit()
    assert select(client,['heat_map:view']).status_code==409
    admin=admin_client(app)
    g.pop('_login_user',None)
    assert admin.post('/modules/request').status_code==403
    owner.parent_franchise_user_id=User.query.filter_by(email='admin@example.com').one().id
    db.session.commit()
    assert select(client,['heat_map:view']).status_code==403
    assert ModuleSubscriptionRequest.query.count()==0


def test_paused_insurance_blocks_existing_paid_access_and_unused_codes(app,franchise,monkeypatch):
    from app.module_licensing import fingerprint, now
    from itsdangerous import URLSafeTimedSerializer
    client,owner=owner_client(app,franchise)
    db.session.add(UserModuleAccess(user_id=owner.id,module_code='insurance_app:view',is_enabled=True,paid_activated_at=now()))
    saved=ModuleActivationCode(user_id=owner.id,module_code='insurance_app:view',issued_by_id=owner.id,code_hash=fingerprint('old-insurance-code'))
    db.session.add(saved)
    db.session.commit()
    assert not owner.has_permission('insurance_app:view')
    assert client.get('/launch/insurance').status_code==403
    client.post('/modules',data={'csrf_token':token(client,'/modules'),'activation_code':'old-insurance-code'})
    assert saved.redeemed_at is None
    monkeypatch.setenv('INSURANCE_LAUNCH_SECRET','paused-module-test')
    signature=URLSafeTimedSerializer('paused-module-test',salt='martins-module-access-v1').dumps({'module':'insurance_app:view','email':owner.email})
    response=client.post('/api/module-access',json={'module':'insurance_app:view','token':signature})
    assert response.status_code==200 and response.json['active'] is False


def test_catalog_migration_seeds_prices_and_downgrades_without_losing_codes():
    spec=importlib.util.spec_from_file_location('catalog_migration',Path(__file__).parents[1]/'migrations/versions/v132_module_catalog.py')
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine=sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        for table in ['users','franchises','module_activation_codes']:
            connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('INSERT INTO module_activation_codes (id) VALUES (7)')
        migration.op=Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.exec_driver_sql('SELECT count(*) FROM module_plans WHERE monthly_premium=40.19').scalar()==2
        assert 'request_item_id' in {c['name'] for c in sa.inspect(connection).get_columns('module_activation_codes')}
        migration.downgrade()
        assert connection.exec_driver_sql('SELECT id FROM module_activation_codes').scalar()==7
        assert 'module_plans' not in sa.inspect(connection).get_table_names()
