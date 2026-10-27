"""Durable Head Office alerts for submitted module subscription requests."""
import json
from datetime import datetime, timezone
from app.extensions import db
from app.jobs import register_job_handler, job_payload, update_job_progress
from app.models import ImportJob, ModuleSubscriptionRequest


def queue_subscription_alert(application):
    db.session.add(ImportJob(kind='module_subscription_head_office_alert', queue_name='territory', priority=10,
        filename='Module subscription request', max_attempts=3,
        payload_json=json.dumps({'request_id': application.id})))


@register_job_handler('module_subscription_head_office_alert')
def send_subscription_alert(job):
    from app.registration import notify
    from app.module_licensing import MODULES
    application = db.session.get(ModuleSubscriptionRequest, job_payload(job).get('request_id'))
    if not application:
        raise RuntimeError('Module subscription request not found.')
    if not application.admin_alert_sent_at:
        lines = '\n'.join(f'- {MODULES.get(item.module_code, item.module_code)}: R{item.monthly_premium:.2f}/month' for item in application.items)
        body = (f'A franchise holder has requested module subscriptions. Request #{application.id}\n\n'
                f'Franchise: {application.franchise.business_name}\nHolder: {application.owner.full_name}\n'
                f'Email: {application.owner.email}\n\n{lines}\n'
                f'Monthly total: R{application.monthly_total:.2f}\n\n'
                'Review at https://martinssystem.co.za/admin/module-activations\n'
                'Confirm payment before issuing a separate activation code for each selected module.')
        if not notify(application, f'Module subscription request #{application.id}', body, 'wjm@martinsdirect.com'):
            raise RuntimeError('Head Office subscription email failed; check mail settings and retry this job.')
        application.admin_alert_sent_at = datetime.now(timezone.utc)
    update_job_progress(job,100,'Head Office subscription request alert delivered.',status='completed',commit=True)
    return {'delivered':True}
