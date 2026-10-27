"""One sign-in, independently authorised Head Office and franchise contexts."""
from flask import abort, g, has_request_context, session
from flask_login import UserMixin
from functools import cached_property
from app.extensions import db
from app.models import FRANCHISE_SCOPED_ROLE_NAMES, Franchise, Role, User, UserAccessProfile


def active(user):
    return bool(user and user.is_active and user.is_active_account)


def office_roles(user):
    return [role for role in user.roles if role.name not in FRANCHISE_SCOPED_ROLE_NAMES]


def choices(identity):
    if not active(identity):
        return []
    result = []
    if office_roles(identity):
        result.append(dict(key='head_office', label='Head Office — ' + ', '.join(role.name for role in office_roles(identity)),
                           principal=identity, franchise=None, profile=None))
    grants = UserAccessProfile.query.filter_by(identity_user_id=identity.id, is_active=True).order_by(UserAccessProfile.id).all()
    branch_ids = set()
    direct_ids = {grant.franchise_id for grant in grants if active(grant.principal)}
    for grant in grants:
        if active(grant.principal) and grant.franchise:
            linked = grant.principal.assigned_franchises if grant.principal.has_role('Franchise User') and not office_roles(grant.principal) else [grant.franchise]
            # The approved owner identity follows its live royalty-group links.
            # Unlinking a branch removes access on the next request.
            if grant.franchise_id not in {branch.id for branch in linked}:
                continue
            for branch in sorted(linked, key=lambda item: item.business_name or ''):
                if branch.id != grant.franchise_id and branch.id in direct_ids:
                    continue
                if branch.id in branch_ids:
                    continue
                key = f'branch:{grant.id}' if branch.id == grant.franchise_id else f'linked:{grant.id}:{branch.id}'
                result.append(dict(key=key, label='Franchise Holder — ' + branch.business_name,
                                   principal=grant.principal, franchise=branch, profile=grant))
                branch_ids.add(branch.id)
    # Holder logins share their approved royalty-group links. Employee logins
    # retain their existing single-branch scope.
    if identity.is_franchise_scoped_user():
        fid = identity.assigned_franchise_id()
        linked = identity.assigned_franchises if identity.has_role('Franchise User') and not office_roles(identity) else [db.session.get(Franchise, fid)] if fid else []
        for branch in sorted(linked, key=lambda item: item.business_name or ''):
            if branch and branch.id not in branch_ids:
                result.append(dict(key=f'own:{branch.id}', label=identity.primary_role_name + ' — ' + branch.business_name,
                                   principal=identity, franchise=branch, profile=None))
    return result


class ProfileUser(UserMixin):
    """Request-local permission view; never mutates persistent User.roles."""
    def __init__(self, identity, choice):
        self.identity_account = identity
        self.principal_account = choice['principal']
        self.access_profile = choice['profile']
        self.profile_key = choice['key']
        self.profile_label = choice['label']
        self.profile_franchise = choice['franchise']

    def __getattr__(self, name):
        return getattr(self.principal_account, name)

    def get_id(self):
        return str(self.identity_account.id)

    @cached_property
    def roles(self):
        if not self.profile_franchise:
            return office_roles(self.identity_account)
        if self.access_profile:
            role = Role.query.filter_by(name='Franchise User').first()
            return [role] if role else []
        return [role for role in self.principal_account.roles if role.name in FRANCHISE_SCOPED_ROLE_NAMES]

    @property
    def primary_role_name(self):
        return self.roles[0].name if self.roles else 'No Role'

    @property
    def is_protected_admin(self):
        return not self.profile_franchise and self.identity_account.is_protected_admin

    @property
    def full_name(self):
        return self.identity_account.full_name

    @property
    def assigned_franchises(self):
        # General screens and employee creation see only the active branch.
        return [self.profile_franchise] if self.profile_franchise else self.principal_account.assigned_franchises

    def assigned_franchise_id(self):
        return self.profile_franchise.id if self.profile_franchise else self.principal_account.assigned_franchise_id()

    def accessible_franchises(self):
        if self.profile_franchise:
            return [self.profile_franchise]
        return User.accessible_franchises(self)

    def can_access_franchise(self, franchise_id):
        return User.can_access_franchise(self, franchise_id)

    def is_admin_user(self):
        return User.is_admin_user(self)

    def is_franchise_scoped_user(self):
        return bool(self.profile_franchise) or User.is_franchise_scoped_user(self)

    def has_role(self, role_name):
        return User.has_role(self, role_name)

    def has_permission(self, code):
        return User.has_permission(self, code)


def resolve(identity, key):
    choice = next((item for item in choices(identity) if item['key'] == key), None)
    return ProfileUser(identity, choice) if choice else None


def session_user(identity):
    if not active(identity):
        return None
    key = session.get('access_profile_key') if has_request_context() else None
    if key:
        # Revocation never falls back to broader Head Office permissions.
        return resolve(identity, key)
    if office_roles(identity):
        if has_request_context() and not session.get('profile_session_version'):
            session.pop('selected_franchise_id', None)
            session.pop('franchise_view_mode', None)
            session['profile_session_version'] = 1
        return resolve(identity, 'head_office')
    return identity


def clear_context():
    # Old form CSRFs, selected branches and filter state cannot cross profiles.
    keep = {key: session[key] for key in ('_user_id', '_fresh', '_id', '_remember') if key in session}
    session.clear()
    session.update(keep)
    for key in list(g):
        if key != '_login_user':
            g.pop(key, None)


def grant_profile(identity, franchise, principal, actor_id):
    if not active(identity) or not active(principal):
        raise ValueError('Activate both the sign-in and franchise owner accounts first.')
    role = Role.query.filter_by(name='Franchise User').first()
    if role is None:
        role = Role(name='Franchise User', is_system_role=True)
        db.session.add(role)
        db.session.flush()
    grant = UserAccessProfile.query.filter_by(identity_user_id=identity.id, franchise_id=franchise.id).first()
    if not grant:
        grant = UserAccessProfile(identity_user_id=identity.id, franchise_id=franchise.id)
        db.session.add(grant)
    grant.principal_user_id = principal.id
    grant.is_active = True
    grant.created_by_id = actor_id
    return grant
