"""Recoverable admin-only activation codes and explicit holder email delivery."""
import base64
import os
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app
from flask_mail import Message
from app.extensions import db
from app.models import User, UserAccessProfile


def cipher():
    configured = current_app.config.get('MODULE_CODE_ENCRYPTION_KEY') or os.getenv('MODULE_CODE_ENCRYPTION_KEY')
    if configured:
        return Fernet(configured)
    secret = current_app.config['SECRET_KEY']
    material = secret.encode() if isinstance(secret, str) else secret
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'martins-module-activation-storage-v1').derive(material)
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value):
    return cipher().encrypt(value.encode()).decode()


def reveal(code):
    if not code.encrypted_code:
        return None  # Legacy fingerprints cannot be reversed.
    try:
        value = cipher().decrypt(code.encrypted_code.encode()).decode()
        from app.module_licensing import fingerprint
        return value if fingerprint(value) == code.code_hash else None
    except (InvalidToken, UnicodeError, ValueError):
        return None


def registered_recipients(owner, request_item=None):
    """Prefer the actual requesting sign-in, then a unique approved identity.

    Do not infer an arbitrary recipient when several people can use the owner
    record. Admin must select from the currently authorised sign-ins instead.
    """
    from app.access_profiles import active, choices
    identities = {}
    for grant in UserAccessProfile.query.filter_by(principal_user_id=owner.id, is_active=True).all():
        identity = grant.identity
        if active(identity) and any(item['franchise'] and item['principal'].id == owner.id for item in choices(identity)):
            identities[identity.id] = identity
    eligible = dict(identities)
    if active(owner):
        eligible[owner.id] = owner
    requester_id = request_item.subscription.requested_by_user_id if request_item else None
    preferred = eligible.get(requester_id)
    if preferred is None:
        preferred = next(iter(identities.values())) if len(identities) == 1 else owner if not identities else None
    ordered = ([preferred] if preferred else []) + [user for user in eligible.values() if user is not preferred]
    options = []
    seen = set()
    for user in ordered:
        email = (user.email or '').strip().lower()
        if email and email not in seen:
            options.append(dict(email=email, label=f'{user.full_name} — {email}', preferred=bool(preferred and user.id == preferred.id)))
            seen.add(email)
    return options


def send_code_email(code, owner):
    from app.module_licensing import MODULES
    from app.registration import RegistrationMailConnection
    value = reveal(code)
    options = registered_recipients(owner, code.request_item)
    if (not value or code.redeemed_at or code.revoked_at or not owner.is_active or not owner.is_active_account
            or code.recipient_email not in {item['email'] for item in options}):
        return False
    code.email_last_attempt_at = datetime.now(timezone.utc)
    code.email_attempts = (code.email_attempts or 0) + 1
    branch_id = code.request_item.subscription.franchise_id if code.request_item else owner.assigned_franchise_id()
    if not branch_id:
        grant = UserAccessProfile.query.filter_by(principal_user_id=owner.id, is_active=True).order_by(UserAccessProfile.id).first()
        branch_id = grant.franchise_id if grant else None
    from app.models import Franchise
    branch = db.session.get(Franchise, branch_id) if branch_id else None
    label = branch.business_name if branch else owner.full_name
    body = (f'Your {MODULES[code.module_code]} subscription activation code is ready.\n\n'
            f'Franchise account: {label}\nActivation code: {value}\n\n'
            f'Sign in at https://martinssystem.co.za using {code.recipient_email} and your existing password.\n'
            f'Choose the franchise holder profile for {label}. Open Activated Modules > Subscriptions, '
            'paste the code into Activate a Paid Module and select Activate Module.\n\n'
            'This code can be used once and activates only the module and franchise account shown above. '
            'The module stays active until Head Office deactivates it. '
            'If you need help selecting the correct franchise profile, contact Head Office.')
    try:
        with RegistrationMailConnection(current_app.extensions['mail']) as connection:
            connection.send(Message(subject=f'{MODULES[code.module_code]} activation code — {label}',
                                    recipients=[code.recipient_email], body=body))
    except Exception as exc:
        code.email_last_error = 'Email could not be sent. Check mail settings and retry.'
        current_app.logger.warning('Activation email failed for record %s (%s)', code.id, type(exc).__name__)
        db.session.commit()
        return False
    code.email_sent_at = datetime.now(timezone.utc)
    code.email_last_error = ''
    db.session.commit()
    return True
