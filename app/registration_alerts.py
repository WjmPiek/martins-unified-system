"""Durable, retryable new-registration email alerts for Head Office."""
import json
from datetime import datetime, timezone
from app.extensions import db
from app.jobs import register_job_handler, job_payload, update_job_progress
from app.models import FranchiseRegistration, ImportJob


def queue_alert(application):
    db.session.add(ImportJob(kind='registration_head_office_alert', queue_name='territory', priority=10,
                            filename='New franchise registration', max_attempts=3,
                            payload_json=json.dumps({'application_id':application.id})))


@register_job_handler('registration_head_office_alert')
def send_alert(job):
    from app.registration import notify
    application = db.session.get(FranchiseRegistration, job_payload(job).get('application_id'))
    if application and not application.admin_alert_sent_at:
        body = (f'A franchise holder has submitted a registration application.\n\n'
                f'Franchise: {application.business_name}\nApplicant: {application.name} {application.surname}\n'
                f'Email: {application.email}\nContact: {application.phone}\n\n'
                'Review it at https://martinssystem.co.za/admin/registrations\n'
                'The applicant has no access until Head Office approves the application.')
        if not notify(application, 'New franchise registration application', body, 'wjm@martinsdirect.com'):
            raise RuntimeError('Head Office email could not be delivered; check mail settings and retry this job.')
        application.admin_alert_sent_at = datetime.now(timezone.utc)
    update_job_progress(job,100,'Head Office registration alert delivered.',status='completed',commit=True)
    return {'delivered':True}
