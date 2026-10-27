"""Head Office explicitly grants ownership contexts; royalty links stay intact."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from app.extensions import db
from app.models import Franchise, User, UserAccessProfile, user_franchises
from app.access_profiles import grant_profile
from app.audit import log_action
from app.registration import check_csrf, csrf_token

profiles_admin_bp = Blueprint('profile_admin', __name__)


def branch_owner(franchise):
    owners = User.query.join(user_franchises, user_franchises.c.user_id == User.id).filter(
        user_franchises.c.franchise_id == franchise.id, user_franchises.c.is_primary.is_(True),
        User.is_active.is_(True), User.is_active_account.is_(True)).all()
    owners = [user for user in owners if user.has_role('Franchise User') and not user.is_admin_user()]
    if not owners:
        owners = [user for user in franchise.assigned_users
                  if user.has_role('Franchise User') and not user.is_admin_user()
                  and user.is_active and user.is_active_account
                  and user.assigned_franchise_id() == franchise.id]
    if len(owners) > 1:
        raise ValueError('This branch has multiple primary owners. Resolve its owner records first.')
    if not owners:
        profiles = UserAccessProfile.query.filter_by(franchise_id=franchise.id, is_active=True).all()
        candidates = {profile.principal.id: profile.principal for profile in profiles
                      if profile.principal.is_active and profile.principal.is_active_account}
        if len(candidates) > 1:
            raise ValueError('This branch has multiple profile owners. Resolve its owner records first.')
        return next(iter(candidates.values()), None)
    return owners[0] if owners else None


@profiles_admin_bp.route('/admin/users/<int:user_id>/profiles', methods=['GET', 'POST'])
@login_required
def manage(user_id):
    if not current_user.is_admin_user():
        abort(403)
    identity = db.session.get(User, user_id)
    if identity is None:
        abort(404)
    if request.method == 'POST':
        check_csrf()
        if request.form.get('action') == 'revoke':
            profile = UserAccessProfile.query.filter_by(id=request.form.get('profile_id', type=int), identity_user_id=identity.id).first_or_404()
            profile.is_active = False
            log_action('Access', 'Revoked franchise profile', f'Login #{identity.id}; franchise #{profile.franchise_id}')
            db.session.commit()
        else:
            if request.form.get('ownership_confirmed') != 'yes':
                abort(400, 'Confirm the verified franchise ownership before granting access.')
            branch = db.session.get(Franchise, request.form.get('franchise_id', type=int))
            if branch is None:
                abort(400, 'Choose the existing franchise.')
            # Lock the selected branch so concurrent grants use one owner record.
            db.session.query(Franchise).filter_by(id=branch.id).with_for_update().one()
            try:
                principal = branch_owner(branch) or identity
                profile = grant_profile(identity, branch, principal, current_user.id)
                db.session.flush()
                log_action('Access', 'Granted franchise profile', f'Login #{identity.id}; owner #{principal.id}; franchise #{branch.id}; profile #{profile.id}')
                db.session.commit()
                flash('Franchise holder profile added. Existing sign-in, roles and royalty records were kept.', 'success')
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
        return redirect(url_for('profile_admin.manage', user_id=user_id))
    return render_template('admin/access_profiles.html', identity=identity,
        profiles=UserAccessProfile.query.filter_by(identity_user_id=identity.id).all(),
        franchises=Franchise.query.order_by(Franchise.business_name).all(), csrf_token=csrf_token())
