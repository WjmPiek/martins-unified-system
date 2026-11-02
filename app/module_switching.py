"""Keep approved profile changes inside the module the user was working in."""
from urllib.parse import parse_qs, urlsplit

from flask import request, url_for
from flask_login import current_user


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
    return {'profile_switch_module': module, 'account_role_label': account_role_label(current_user) if current_user.is_authenticated else '',
            'profile_switch_url': url_for('auth.profiles', **({'module': module} if module else {}))}


MODULE_PERMISSIONS = {'attendance': 'attendance:view', 'heatmap': 'heat_map:view',
                      'manuals': 'manuals:view', 'claims': 'insurance_claims:view',
                      'insurance': 'insurance_app:view'}


def module_available(user, module):
    code = MODULE_PERMISSIONS.get(module)
    return bool(code and user.has_permission(code))


def popup_switch():
    if request.values.get('popup') == '1':
        return True
    try:
        target = urlsplit(request.args.get('next', ''))
    except ValueError:
        return False
    return not target.scheme and not target.netloc and target.path == '/profiles' and parse_qs(target.query).get('popup') == ['1']


def account_role_label(user):
    names = {'Franchise User': 'Franchise Holder', 'Super Admin': 'Administrator', 'Admin': 'Administrator',
             'Manager User': 'Manager', 'Employee User': 'Employee'}
    return ', '.join(dict.fromkeys(names.get(role.name, role.name) for role in user.roles)) or 'User'


def popup_origin():
    import os
    own = urlsplit(request.host_url)
    own_origin = f'{own.scheme}://{own.netloc}'
    attendance = urlsplit(os.getenv('ATTENDANCE_APP_URL', 'https://name-attendance-register-frontend.onrender.com'))
    claims = urlsplit(os.getenv('CLAIMS_APP_URL', 'https://franchise-claims-system.onrender.com'))
    allowed = {own_origin, f'https://{own.netloc}', f'{attendance.scheme}://{attendance.netloc}', f'{claims.scheme}://{claims.netloc}'}
    value = request.values.get('opener_origin', '')
    if not value:
        try:
            target = urlsplit(request.args.get('next', ''))
            if not target.scheme and not target.netloc and target.path == '/profiles':
                value = parse_qs(target.query).get('opener_origin', [''])[0]
        except ValueError:
            pass
    return value if value in allowed else own_origin
