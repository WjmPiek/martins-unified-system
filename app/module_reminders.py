"""Owner-only trial reminders shared by every page in the main system."""
from datetime import timedelta, timezone
from math import ceil
from app.module_licensing import MODULES, now, utc


def trial_warning(user):
    if (not user.is_authenticated or user.is_admin_user() or not user.has_role('Franchise User')
            or user.parent_franchise_user_id or not user.is_active or not user.is_active_account):
        return None
    clock = now()
    items = []
    for access in user.module_access:
        if access.module_code not in MODULES or not access.is_enabled or access.paid_activated_at or not access.trial_expires_at:
            continue
        remaining = (utc(access.trial_expires_at) - clock).total_seconds()
        if 0 < remaining <= 5 * 86400:
            items.append({'name': MODULES[access.module_code], 'days': ceil(remaining / 86400),
                          'deadline': utc(access.trial_expires_at).isoformat()})
    if not items:
        return None
    day = clock.astimezone(timezone(timedelta(hours=2))).date().isoformat()
    return {'items': items, 'dismiss_key': f'module-trial:{user.id}:{day}:' + '|'.join(item['deadline'] for item in items)}
