"""Keep approved profile changes inside the module the user was working in."""
from urllib.parse import parse_qs, urlsplit

from flask import request, url_for


MODULE_DESTINATIONS = {
    'attendance': ('Attendance', 'attendance_launch.launch'),
    'heatmap': ('Heat Map', 'heatmap.index'),
    'manuals': ('Manuals', 'manuals.index'),
    'claims': ('Claims', 'claims_launch.launch'),
    'insurance': ('Insurance Applications', 'insurance_launch.launch'),
}
BLUEPRINT_MODULES = {
    'attendance': 'attendance', 'attendance_launch': 'attendance',
    'heatmap': 'heatmap', 'manuals': 'manuals', 'insurance_claims': 'claims',
    'claims_launch': 'claims', 'insurance_launch': 'insurance',
}


def requested_module():
    value = request.form.get('return_module') or request.args.get('module') or ''
    if value in MODULE_DESTINATIONS:
        return value
    # Flask-Login carries a protected /profiles request through sign-in.
    # Read only our allowlisted module name, never redirect to the supplied URL.
    try:
        target = urlsplit(request.args.get('next', ''))
    except ValueError:
        return ''
    if not target.scheme and not target.netloc and target.path == '/profiles':
        value = parse_qs(target.query).get('module', [''])[0]
        if value in MODULE_DESTINATIONS:
            return value
    return ''


def module_destination(module):
    entry = MODULE_DESTINATIONS.get(module)
    return url_for(entry[1]) if entry else None


def switcher_context():
    module = BLUEPRINT_MODULES.get(request.blueprint, '')
    return {'profile_switch_url': url_for('auth.profiles', **({'module': module} if module else {}))}
