"""Public applications are separate from users until reviewed by Head Office."""
import secrets
import smtplib
import ssl
from datetime import date, datetime, timezone

from email_validator import validate_email, EmailNotValidError
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from flask_mail import Connection, Message
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import User, Role, Franchise, FranchiseRegistration, user_franchises, ensure_mandatory_franchise_modules

registration_bp = Blueprint('registration', __name__)


class RegistrationMailConnection(Connection):
    def configure_host(self):
        # A mail outage must not leave registration/review waiting indefinitely.
        if self.mail.use_ssl:
            host = smtplib.SMTP_SSL(self.mail.server, self.mail.port, timeout=5, context=ssl.create_default_context())
        else:
            host = smtplib.SMTP(self.mail.server, self.mail.port, timeout=5)
        try:
            if self.mail.use_tls:
                host.starttls(context=ssl.create_default_context())
            if self.mail.username and self.mail.password:
                host.login(self.mail.username, self.mail.password)
        except Exception:
            host.close()
            raise
        return host


def csrf_token():
    if 'registration_csrf' not in session:
        session['registration_csrf'] = secrets.token_urlsafe(32)
    return session['registration_csrf']


def check_csrf():
    expected = session.get('registration_csrf', '')
    if not expected or not secrets.compare_digest(expected, request.form.get('csrf_token', '')):
        abort(400, 'This form expired. Refresh the page and try again.')


def notify(application, subject, body, recipient=None):
    try:
        with RegistrationMailConnection(current_app.extensions['mail']) as connection:
            connection.send(Message(subject=subject, recipients=[recipient or application.email], body=body))
        return True
    except Exception:
        current_app.logger.warning('Registration email delivery failed for application %s', application.id)
        return False


def submit_registration():
    if current_user.is_authenticated:
        return redirect(url_for('auth.home'))
    error = None
    if request.method == 'POST':
        check_csrf()
        fields = {key: request.form.get(key, '').strip() for key in ('name', 'surname', 'email', 'business_name', 'phone')}
        password = request.form.get('password', '')
        try:
            fields['email'] = validate_email(fields['email'], check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            error = 'Enter a valid email address.'
        if not all(fields.values()) or any(len(value) > 120 for key, value in fields.items() if key != 'email'):
            error = 'Complete all contact and franchise fields (maximum 120 characters each).'
        elif len(password) < 8 or len(password) > 128:
            error = 'Choose a password between 8 and 128 characters.'
        elif password != request.form.get('confirm_password', ''):
            error = 'The passwords do not match.'
        elif request.form.get('confirm_application') != 'yes':
            error = 'Confirm that you are requesting access for this franchise.'
        elif fields['email'] == 'wjm@martinsdirect.com':
            error = 'This email is already registered. Please use Log in or Forgot password.'
        if not error:
            existing = User.query.filter(db.func.lower(User.email) == fields['email']).first()
            application = FranchiseRegistration.query.filter_by(email=fields['email']).first()
            if existing or application:
                error = 'This email already has an account or application. Contact Head Office for help, or use Log in / Forgot password if already approved.'
            else:
                application = FranchiseRegistration(**fields, password_hash=generate_password_hash(password))
                db.session.add(application)
                try:
                    db.session.flush()
                    from app.registration_alerts import queue_alert
                    queue_alert(application)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    error = 'An application for this email already exists. Contact Head Office.'
                if not error:
                    # Never include passwords in email or expose applications publicly.
                    notify(application, "Martin's Funerals registration received",
                           'Your franchise access application has been received. Head Office must verify your details and approve access before you can log in. Contact Head Office if you need an update.')
                    return redirect(url_for('registration.received'))
    return render_template('auth/register.html', error=error, registration_csrf=csrf_token())


@registration_bp.get('/registration/received')
def received():
    return render_template('auth/registration_received.html')


def require_reviewer():
    if not current_user.is_admin_user():
        abort(403)


@registration_bp.route('/admin/registrations', methods=['GET'])
@login_required
def applications():
    require_reviewer()
    return render_template('admin/registrations.html',
                           applications=FranchiseRegistration.query.order_by(FranchiseRegistration.created_at.desc()).all(),
                           franchises=Franchise.query.order_by(Franchise.business_name).all(),
                           registration_csrf=csrf_token())


@registration_bp.post('/admin/registrations/<int:application_id>/review')
@login_required
def review(application_id):
    require_reviewer()
    check_csrf()
    application = FranchiseRegistration.query.filter_by(id=application_id).with_for_update().first_or_404()
    if application.status != 'pending':
        flash('This application has already been reviewed.', 'warning')
        return redirect(url_for('registration.applications'))
    action = request.form.get('action')
    if action not in {'approve', 'reject'}:
        abort(400)
    if not request.form.get('review_confirmed'):
        flash('Verify the applicant and franchise ownership before reviewing.', 'danger')
        return redirect(url_for('registration.applications'))
    if action == 'approve':
        if User.query.filter(db.func.lower(User.email) == application.email).first():
            flash('An account already uses this email. Resolve the existing account before approval.', 'danger')
            return redirect(url_for('registration.applications'))
        franchise_id = request.form.get('franchise_id', '')
        if franchise_id == 'new':
            if Franchise.query.filter(db.func.lower(Franchise.business_name) == application.business_name.lower()).first():
                flash('A franchise with that name exists. Select its existing record.', 'danger')
                return redirect(url_for('registration.applications'))
            try:
                start = date.fromisoformat(request.form.get('agreement_start_date', ''))
                end = date.fromisoformat(request.form.get('agreement_end_date', ''))
                if end < start:
                    raise ValueError()
            except ValueError:
                flash('Enter valid agreement start and end dates for the new franchise.', 'danger')
                return redirect(url_for('registration.applications'))
            franchise = Franchise(business_name=application.business_name, franchisee_name=application.name,
                                  franchisee_surname=application.surname, franchisee_email=application.email,
                                  franchisee_cell=application.phone, agreement_start_date=start, agreement_end_date=end,
                                  royalty_gross_method='new' if start.year >= 2018 else 'old', is_performance_active=True)
            db.session.add(franchise)
            db.session.flush()
        else:
            try:
                franchise = db.session.get(Franchise, int(franchise_id))
            except (TypeError, ValueError):
                franchise = None
            if not franchise:
                flash('Select the correct franchise before approval.', 'danger')
                return redirect(url_for('registration.applications'))
        # Lock the franchise as well: two applications must not claim one owner slot.
        db.session.query(Franchise).filter_by(id=franchise.id).with_for_update().one()
        owners = [u for u in franchise.assigned_users if u.has_role('Franchise User') and u.is_active_account]
        if owners:
            db.session.rollback()
            flash('This franchise already has an active owner. Resolve its existing owner access before approving another application.', 'danger')
            return redirect(url_for('registration.applications'))
        role = Role.query.filter_by(name='Franchise User').first()
        if not role:
            role = Role(name='Franchise User', is_system_role=True)
            db.session.add(role)
        user = User(name=application.name, surname=application.surname, email=application.email,
                    password_hash=application.password_hash, is_active=True, is_active_account=True,
                    created_by_user_id=current_user.id, roles=[role], assigned_franchises=[franchise])
        db.session.add(user)
        db.session.flush()
        ensure_mandatory_franchise_modules(user)
        db.session.execute(user_franchises.update().where(user_franchises.c.user_id == user.id)
                           .where(user_franchises.c.franchise_id == franchise.id).values(is_primary=True))
        application.user_id = user.id
        application.status = 'approved'
    else:
        application.status = 'rejected'
    application.reviewed_by_id = current_user.id
    application.reviewed_at = datetime.now(timezone.utc)
    application.review_note = request.form.get('review_note', '').strip()[:1000]
    application.password_hash = ''  # The approved user holds the hash; rejected applications cannot authenticate.
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('The account changed during review. Refresh and check the existing account before trying again.', 'danger')
        return redirect(url_for('registration.applications'))
    if action == 'approve':
        body = f"Head Office has approved your franchise access. Log in at {url_for('auth.login', _external=True)} using your email and the password you chose when applying. If you forgot it, use Forgot password."
    else:
        body = 'Head Office has reviewed your application and has not approved access. Please contact Head Office for assistance.'
    sent = notify(application, "Martin's Funerals registration update", body)
    flash(f'Application {application.status}. ' + ('Applicant notified by email.' if sent else 'Email could not be sent. Please contact the applicant directly.'), 'success' if sent else 'warning')
    return redirect(url_for('registration.applications'))


@registration_bp.post('/admin/registrations/<int:application_id>/email-alert')
@login_required
def retry_head_office_alert(application_id):
    require_reviewer()
    check_csrf()
    application = FranchiseRegistration.query.filter_by(id=application_id).with_for_update().first_or_404()
    if not application.admin_alert_sent_at:
        from app.registration_alerts import queue_alert
        from app.models import ImportJob
        import json
        pending = ImportJob.query.filter_by(kind='registration_head_office_alert').filter(ImportJob.status.in_(['queued','running','processing'])).all()
        if not any(json.loads(job.payload_json or '{}').get('application_id') == application.id for job in pending):
            queue_alert(application)
            db.session.commit()
    flash('Head Office email alert queued, already waiting, or previously delivered. Check Operations Centre for delivery errors.', 'info')
    return redirect(url_for('registration.applications'))
