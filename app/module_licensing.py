"""Time-limited trials and owner-bound, single-use paid activation codes."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import os

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer, BadSignature

from app.extensions import db
from app.models import User, Role, UserModuleAccess, ModuleActivationCode, OPTIONAL_FRANCHISE_MODULES
from app.registration import csrf_token, check_csrf

licensing_bp = Blueprint('licensing', __name__)
# Insurance Applications is paused while Head Office builds the separate system.
MODULES = {'heat_map:view':'Heat Map', 'attendance:view':'Attendance'}


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def license_owner(user):
    if user.has_role('Franchise User'):
        return user
    if user.parent_franchise_user_id:
        return db.session.get(User, user.parent_franchise_user_id)
    fid = user.assigned_franchise_id()
    if not fid:
        return None
    from app.models import user_franchises
    return User.query.join(user_franchises, user_franchises.c.user_id == User.id).join(User.roles).filter(
        user_franchises.c.franchise_id == fid, Role.name == 'Franchise User', User.is_active_account.is_(True)
    ).order_by(user_franchises.c.is_primary.desc(), User.id).first()


def module_active(user, code):
    if code not in MODULES:
        return False
    owner = license_owner(user)
    if not owner or not owner.is_active or not owner.is_active_account:
        return False
    access = next((a for a in owner.module_access if a.module_code == code), None)
    return bool(access and access.is_enabled and (access.paid_activated_at or
                (access.trial_expires_at and utc(access.trial_expires_at) > now())))


def start_trial(user):
    if not user.has_role('Franchise User') or user.is_admin_user() or user.module_trial_started_at:
        return
    user.module_trial_started_at = now()
    expires = user.module_trial_started_at + timedelta(days=30)
    for code in MODULES:
        access = next((a for a in user.module_access if a.module_code == code), None)
        if access is None:
            access = UserModuleAccess(user=user, module_code=code)
            db.session.add(access)
        access.is_enabled = True
        access.trial_expires_at = expires


def fingerprint(value):
    normalized = ''.join(c for c in value.upper() if c not in '- \t\r\n')
    return hashlib.sha256(normalized.encode()).hexdigest()


@licensing_bp.route('/modules', methods=['GET', 'POST'])
@login_required
def modules():
    if not current_user.has_role('Franchise User') or current_user.is_admin_user():
        abort(403)
    if request.method == 'POST':
        check_csrf()
        User.query.filter_by(id=current_user.id).with_for_update().one()
        value = request.form.get('activation_code', '')[:200]
        code = ModuleActivationCode.query.filter_by(code_hash=fingerprint(value), user_id=current_user.id).with_for_update().first()
        if not code or code.module_code not in MODULES or code.redeemed_at or code.revoked_at:
            flash('This activation code is invalid, already used, or has been cancelled. Contact Head Office.', 'danger')
        else:
            access = UserModuleAccess.query.filter_by(user_id=current_user.id, module_code=code.module_code).with_for_update().first()
            if access is None:
                access = UserModuleAccess(user_id=current_user.id, module_code=code.module_code)
                db.session.add(access)
            access.is_enabled = True
            access.paid_activated_at = now()
            code.redeemed_at = now()
            if code.request_item:
                code.request_item.status = 'active'
            db.session.commit()
            flash(f'{MODULES[code.module_code]} activated. Access remains active until Head Office deactivates it.', 'success')
        return redirect(url_for('licensing.modules'))
    from app.module_catalog import catalog, owner_requests, selection_state
    from app.models import Franchise
    pending, paid = selection_state(current_user)
    return render_template('modules/index.html', modules=MODULES, owner=current_user,
                           catalog=catalog(), subscription_requests=owner_requests(current_user), pending_modules=pending,
                           paid_modules=paid, primary_franchise=db.session.get(Franchise, current_user.assigned_franchise_id()),
                           module_active=module_active, registration_csrf=csrf_token(), utc=utc, now=now())


@licensing_bp.route('/admin/module-activations', methods=['GET', 'POST'])
@login_required
def manage():
    if not current_user.is_admin_user():
        abort(403)
    generated = None
    if request.method == 'POST':
        check_csrf()
        if request.form.get('action') == 'set_price':
            from app.module_catalog import update_price
            return update_price()
        user = User.query.filter_by(id=request.form.get('user_id', type=int)).with_for_update().first_or_404()
        code = request.form.get('module_code')
        if not user.has_role('Franchise User') or user.is_admin_user() or code not in MODULES:
            abort(400)
        action = request.form.get('action')
        if action == 'issue':
            if not user.is_active_account or not user.is_active or request.form.get('payment_confirmed') != 'yes':
                flash('Confirm payment and make sure the franchise account is active before issuing a code.', 'danger')
                return redirect(url_for('licensing.manage'))
            raw = secrets.token_hex(16).upper()
            generated = '-'.join(raw[i:i+8] for i in range(0,32,8))
            from app.module_catalog import pending_item
            item = pending_item(user.id, code)
            if item:
                item.status = 'code_issued'
            db.session.add(ModuleActivationCode(user_id=user.id, module_code=code, code_hash=fingerprint(generated),
                                               issued_by_id=current_user.id, payment_reference=request.form.get('payment_reference','').strip()[:160],
                                               request_item=item))
            db.session.commit()
            generated = {'value':generated, 'email':user.email, 'module':MODULES[code]}
        elif action == 'deactivate':
            from app.models import ModuleSubscriptionItem, ModuleSubscriptionRequest
            for selection in ModuleSubscriptionItem.query.join(ModuleSubscriptionRequest).filter(
                ModuleSubscriptionRequest.owner_user_id == user.id,
                ModuleSubscriptionItem.module_code == code,
                ModuleSubscriptionItem.status.in_(['pending', 'code_issued', 'active'])
            ).with_for_update().all():
                selection.status = 'deactivated'
            access = UserModuleAccess.query.filter_by(user_id=user.id, module_code=code).with_for_update().first()
            if access:
                access.is_enabled = False
            # Cancel unused codes too, so an older code cannot undo deactivation.
            for item in ModuleActivationCode.query.filter_by(user_id=user.id, module_code=code, redeemed_at=None, revoked_at=None).with_for_update().all():
                item.revoked_at = now()
            db.session.commit()
            flash(f'{MODULES[code]} deactivated for {user.full_name}.', 'success')
            return redirect(url_for('licensing.manage'))
        else:
            abort(400)
    owners = User.query.join(User.roles).filter(Role.name == 'Franchise User').order_by(User.name,User.surname).all()
    from app.module_catalog import admin_catalog_context
    response = render_template('modules/manage.html', modules=MODULES, owners=owners, generated=generated,
                               selected_owner_id=request.args.get('user_id', type=int), selected_module_code=request.args.get('module_code'),
                               module_active=module_active, registration_csrf=csrf_token(), utc=utc, now=now(), **admin_catalog_context())
    return response, 200, {'Cache-Control':'no-store', 'Referrer-Policy':'same-origin'}


@licensing_bp.post('/api/module-access')
def check_external_access():
    data = request.get_json(silent=True) or {}
    module = data.get('module')
    key = {'attendance:view':'ATTENDANCE_LAUNCH_SECRET','insurance_app:view':'INSURANCE_LAUNCH_SECRET'}.get(module)
    secret = os.getenv(key, '').strip() if key else ''
    if not secret:
        abort(403)
    try:
        signed = URLSafeTimedSerializer(secret, salt='martins-module-access-v1').loads(data.get('token',''), max_age=60)
    except (BadSignature, TypeError):
        abort(403)
    if signed.get('module') != module or not signed.get('email'):
        abort(403)
    user = User.query.filter(db.func.lower(User.email) == str(signed['email']).strip().lower()).first()
    # Unknown identities must not be granted an entitlement by this endpoint.
    active = bool(user and user.is_active and user.is_active_account and user.has_permission(module))
    expires = None
    if user and user.is_franchise_scoped_user():
        owner = license_owner(user)
        access = next((a for a in owner.module_access if a.module_code == module), None) if owner else None
        if access and not access.paid_activated_at and access.trial_expires_at:
            expires = utc(access.trial_expires_at).timestamp()
    return jsonify(active=active, managed=bool(user and user.is_franchise_scoped_user()), expires_at=expires), 200, {'Cache-Control':'no-store'}
