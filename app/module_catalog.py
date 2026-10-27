"""Owner-bound module selections and Head Office monthly pricing."""
from decimal import Decimal, InvalidOperation
from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required
from app.extensions import db
from app.models import User, ModulePlan, ModuleSubscriptionRequest, ModuleSubscriptionItem
from app.module_licensing import licensing_bp, MODULES
from app.registration import check_csrf

DESCRIPTIONS = {
    'heat_map:view': 'Explore client locations, coverage and franchise density on an interactive map.',
    'attendance:view': 'Manage staff attendance, sign-ins, leave and workforce reporting.',
}


def catalog():
    plans = {plan.module_code: plan for plan in ModulePlan.query.all()}
    return [dict(code=code, name=name, description=DESCRIPTIONS[code],
                 price=plans[code].monthly_premium, cents=int(plans[code].monthly_premium * 100))
            for code, name in MODULES.items()]


def can_edit_prices(user):
    return user.is_admin_user() and (user.email or '').strip().lower() == 'wjm@martinsdirect.com'


def update_price():
    if not can_edit_prices(current_user):
        abort(403)
    code = request.form.get('module_code')
    if code not in MODULES:
        abort(400)
    try:
        price = Decimal(request.form.get('monthly_premium', '').strip())
        if not price.is_finite() or price < 0 or price > Decimal('9999999999.99') or price != price.quantize(Decimal('0.01')):
            raise ValueError()
    except (InvalidOperation, ValueError):
        flash('Enter a valid monthly premium with no more than two decimal places.', 'danger')
        return redirect(url_for('licensing.manage'))
    plan = ModulePlan.query.filter_by(module_code=code).with_for_update().first_or_404()
    plan.monthly_premium = price
    plan.updated_by_id = current_user.id
    db.session.commit()
    flash(f'{MODULES[code]} monthly premium updated to R{price:.2f}. Existing request prices are retained.', 'success')
    return redirect(url_for('licensing.manage'))


def owner_requests(owner):
    return ModuleSubscriptionRequest.query.filter_by(owner_user_id=owner.id).order_by(ModuleSubscriptionRequest.created_at.desc()).all()


def selection_state(owner):
    pending = {item.module_code for application in owner_requests(owner) for item in application.items
               if item.status in {'pending', 'code_issued'}}
    paid = {access.module_code for access in owner.module_access if access.is_enabled and access.paid_activated_at}
    return pending, paid


@licensing_bp.post('/modules/request')
@login_required
def request_modules():
    if not current_user.has_role('Franchise User') or current_user.is_admin_user() or current_user.parent_franchise_user_id:
        abort(403)
    check_csrf()
    owner = User.query.filter_by(id=current_user.id).with_for_update().one()
    franchise_id = current_user.assigned_franchise_id()
    if not franchise_id or not owner.is_active or not owner.is_active_account:
        abort(409, 'Head Office must link and activate your main franchise account first.')
    selected = set(request.form.getlist('module_codes'))
    if not selected or not selected.issubset(MODULES):
        flash('Choose one or more available modules.', 'danger')
        return redirect(url_for('licensing.modules'))
    pending, paid = selection_state(owner)
    if selected & (pending | paid):
        flash('One of the selected modules is already requested or paid and active. Refresh your choices.', 'warning')
        return redirect(url_for('licensing.modules'))
    plans = ModulePlan.query.filter(ModulePlan.module_code.in_(selected)).with_for_update().all()
    if len(plans) != len(selected):
        abort(409, 'Module pricing is being configured. Please contact Head Office.')
    actor = current_user._get_current_object()
    identity = getattr(actor, 'identity_account', actor)
    application = ModuleSubscriptionRequest(owner_user_id=owner.id, franchise_id=franchise_id, requested_by_user_id=identity.id)
    application.items = [ModuleSubscriptionItem(module_code=plan.module_code, monthly_premium=plan.monthly_premium)
                         for plan in plans]
    db.session.add(application)
    db.session.flush()
    from app.module_alerts import queue_subscription_alert
    queue_subscription_alert(application)
    db.session.commit()
    flash(f'Module request #{application.id} submitted for your main franchise. Monthly total: R{application.monthly_total:.2f}. Head Office will confirm payment before issuing activation codes.', 'success')
    return redirect(url_for('licensing.modules'))


def pending_item(owner_id, module_code, lock=True):
    query = ModuleSubscriptionItem.query.join(ModuleSubscriptionRequest).filter(
        ModuleSubscriptionRequest.owner_user_id == owner_id,
        ModuleSubscriptionItem.module_code == module_code,
        ModuleSubscriptionItem.status.in_(['pending', 'code_issued'])
    ).order_by(ModuleSubscriptionItem.id)
    return query.with_for_update().first() if lock else query.first()


def admin_catalog_context(owner_ids=None):
    requests = ModuleSubscriptionRequest.query
    if owner_ids is not None:
        requests = requests.filter(ModuleSubscriptionRequest.owner_user_id.in_(owner_ids))
    return dict(catalog=catalog(), can_edit_prices=can_edit_prices(current_user),
                subscription_requests=requests.order_by(ModuleSubscriptionRequest.created_at.desc()).all())
