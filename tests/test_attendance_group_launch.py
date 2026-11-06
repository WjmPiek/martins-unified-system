from types import SimpleNamespace
import json
import pytest
from urllib.parse import urlparse, parse_qs
from flask import Flask
from itsdangerous import URLSafeTimedSerializer
from app.attendance_launch import routes
from test_native_territory import app


def test_attendance_handoff_uses_holder_group_without_expanding_active_branch(monkeypatch):
    first = SimpleNamespace(id=1, business_name='Branch One', vat_number='4000000000')
    second = SimpleNamespace(id=2, business_name='Branch Two', vat_number='4000000000')
    owner = SimpleNamespace(id=100,email='owner@example.test',assigned_franchises=[first, second],assigned_franchise_id=lambda:1,has_permission=lambda code:code=='attendance:view')
    user = SimpleNamespace(email='owner@example.test', full_name='Example Owner', roles=[SimpleNamespace(name='Franchise User')], principal_account=owner,
                           has_permission=lambda code: code == 'attendance:view')
    monkeypatch.setattr(routes, 'current_user', user)
    monkeypatch.setattr(routes,'attendance_holder_for',lambda user,branch:owner)
    monkeypatch.setattr(routes, 'get_accessible_franchises', lambda: [first])
    monkeypatch.setenv('ATTENDANCE_APP_URL', 'https://attendance.example.test')
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET', 'test-only-launch-secret')
    app = Flask(__name__)
    with app.test_request_context('/launch/attendance?frame=1'):
        response = routes.launch.__wrapped__()
    token = parse_qs(urlparse(response.location).query)['martins_launch'][0]
    data = URLSafeTimedSerializer('test-only-launch-secret', salt='martins-attendance-launch-v1').loads(token)
    assert data['franchises'] == ['Branch One']
    assert data['branch_scope'] == [{'id':1,'name':'Branch One','vat_number':'4000000000'}, {'id':2,'name':'Branch Two','vat_number':'4000000000'}]
    assert data['is_admin'] is False
    assert data['active_franchise_id']==1 and data['primary_franchise_id']==1
    assert data['business_profile']['franchise_id']==1


def test_nonowner_launch_does_not_inherit_a_principals_group(monkeypatch):
    user = SimpleNamespace(email='staff@example.test', full_name='Example Staff', roles=[SimpleNamespace(name='Read Only User')],
                           has_permission=lambda code: code == 'attendance:view')
    monkeypatch.setattr(routes, 'current_user', user)
    monkeypatch.setattr(routes,'attendance_holder_for',lambda user,branch:user)
    monkeypatch.setattr(routes, 'get_accessible_franchises', lambda: [])
    monkeypatch.setenv('ATTENDANCE_APP_URL', 'https://attendance.example.test')
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET', 'test-only-launch-secret')
    with Flask(__name__).test_request_context('/launch/attendance?frame=1'):
        response = routes.launch.__wrapped__()
    token = parse_qs(urlparse(response.location).query)['martins_launch'][0]
    data = URLSafeTimedSerializer('test-only-launch-secret', salt='martins-attendance-launch-v1').loads(token)
    assert data['branch_scope'] == []
    assert data['business_profile'] is None


def test_primary_business_details_map_registration_and_contact_fields():
    primary=SimpleNamespace(id=1,business_name='Northcliff',pty_business_name='Example Pty Ltd',pty_number='2026/000001/07',
                            vat_number='4000000000',office_address='Example address',office_number='0100000000',
                            after_hours_number='0800000000',franchisee_cell='0700000000',website_url='https://example.test')
    details=routes._business_profile(primary)
    assert details['franchise_name']=='Northcliff' and details['business_name']=='Example Pty Ltd'
    assert details['business_registration_number']=='2026/000001/07'
    assert details['vat_number']=='4000000000' and details['office_address']=='Example address'
    assert details['twenty_four_hour_number']=='0800000000' and details['contact_number']=='0700000000'
    assert details['website']=='https://example.test'


def test_secondary_profile_resolves_only_to_an_approved_top_level_holder(monkeypatch):
    from app import access_profiles
    first=SimpleNamespace(id=1);second=SimpleNamespace(id=2);third=SimpleNamespace(id=3)
    role=SimpleNamespace(name='Franchise User')
    def owner(uid,primary,branches):
        return SimpleNamespace(id=uid,roles=[role],has_role=lambda name:name=='Franchise User',assigned_franchises=branches,assigned_franchise_id=lambda:primary)
    main=owner(100,1,[first,second,third]);secondary=owner(200,2,[second,third])
    user=SimpleNamespace(principal_account=secondary,identity_account=SimpleNamespace(id=1),roles=[role],has_role=lambda name:name=='Franchise User',profile_franchise=second)
    monkeypatch.setattr(access_profiles,'choices',lambda identity:[{'principal':main},{'principal':secondary}])
    assert access_profiles.attendance_holder_for(user,second).id==100
    # No lookup by name or unapproved owner grants is allowed.
    monkeypatch.setattr(access_profiles,'choices',lambda identity:[{'principal':secondary}])
    assert access_profiles.attendance_holder_for(user,second).id==200


def test_ambiguous_overlapping_holder_groups_are_not_guessed(monkeypatch):
    import pytest
    from app import access_profiles
    a,b,c=[SimpleNamespace(id=i) for i in (1,2,3)]
    role=SimpleNamespace(name='Franchise User')
    def owner(uid,primary,branches):
        return SimpleNamespace(id=uid,roles=[role],has_role=lambda name:True,assigned_franchises=branches,assigned_franchise_id=lambda:primary)
    one,two=owner(100,1,[a,b]),owner(200,3,[c,b])
    user=SimpleNamespace(principal_account=one,identity_account=SimpleNamespace(id=1),roles=[role],has_role=lambda name:True)
    monkeypatch.setattr(access_profiles,'choices',lambda identity:[{'principal':one},{'principal':two}])
    with pytest.raises(ValueError,match='ambiguous'):
        access_profiles.attendance_holder_for(user,b)


@pytest.mark.parametrize('explicit_group',[False,True])
def test_linked_branch_profile_uses_approved_holder_attendance_without_head_office_access(app,monkeypatch,explicit_group):
    from test_access_profiles import setup_accounts,sign_in,call
    from app.access_profiles import choices,grant_profile,ProfileUser
    from app.extensions import db
    from app.models import User,user_franchises
    from app.module_licensing import start_trial
    identity,owner,branches,main_grant=setup_accounts(app)
    secondary=User(name='Benoni',surname='Holder',email='secondary@example.test',roles=list(owner.roles),assigned_franchises=[branches[1]])
    secondary.set_password('test-only-password')
    db.session.add(secondary);db.session.flush()
    db.session.execute(user_franchises.update().where(user_franchises.c.user_id==secondary.id).values(is_primary=True))
    grant=grant_profile(identity,branches[1],secondary,identity.id)
    if explicit_group:
        owner.assigned_franchises=[branches[0]]
        monkeypatch.setenv('ATTENDANCE_HOLDER_BRANCH_GROUPS',json.dumps({str(owner.id):[b.id for b in branches]}))
    start_trial(owner);db.session.commit()
    choice=next(c for c in choices(identity) if c['profile'] and c['profile'].id==grant.id)
    profile=ProfileUser(identity,choice)
    assert profile.attendance_owner_account.id==owner.id
    assert profile.has_permission('attendance:view')
    assert not profile.is_admin_user()
    assert [b.id for b in profile.accessible_franchises()]==[branches[1].id]
    assert not secondary.has_permission('attendance:view')
    monkeypatch.setenv('ATTENDANCE_APP_URL','https://attendance.example.test')
    monkeypatch.setenv('ATTENDANCE_LAUNCH_SECRET','test-only-launch-secret')
    client=app.test_client();sign_in(client,choice['key'])
    page=call(client,'get','/launch/attendance')
    assert page.status_code==200
    assert 'id="attendance-module"' in page.text
    assert 'src="/launch/attendance?frame=1"' in page.text
    assert 'data-module-switch' in page.text
    assert 'geolocation https://attendance.example.test' in page.text
    response=call(client,'get','/launch/attendance?frame=1')
    assert response.status_code==302
    token=parse_qs(urlparse(response.location).query)['martins_launch'][0]
    payload=URLSafeTimedSerializer('test-only-launch-secret',salt='martins-attendance-launch-v1').loads(token)
    assert payload['email']==owner.email and payload['active_franchise_id']==branches[1].id
    assert payload['is_admin'] is False
    assert {b['id'] for b in payload['branch_scope']}=={b.id for b in (branches if explicit_group else branches[:2])}
    assert {b.id for b in owner.assigned_franchises}=={b.id for b in (branches[:1] if explicit_group else branches[:2])}
    query=parse_qs(urlparse(response.location).query)
    assert query['embedded']==['1']
    assert response.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('configured',['invalid','[]','{"OWNER":[999999]}','{"OWNER":[true]}'])
def test_invalid_attendance_group_fails_closed(app,monkeypatch,configured):
    from test_access_profiles import setup_accounts
    from app.attendance_groups import attendance_group_branches
    _,owner,_,_=setup_accounts(app)
    monkeypatch.setenv('ATTENDANCE_HOLDER_BRANCH_GROUPS',configured.replace('OWNER',str(owner.id)))
    with pytest.raises(ValueError,match='Head Office'):
        attendance_group_branches(owner)
