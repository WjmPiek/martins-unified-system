"""Explicit Attendance-only holder groups; other module ownership stays independent."""
import json
import os

from app.models import Franchise


def attendance_group_branches(owner):
    assigned=list(owner.assigned_franchises)
    raw=os.getenv('ATTENDANCE_HOLDER_BRANCH_GROUPS','').strip()
    if not raw:
        return assigned
    try:
        groups=json.loads(raw)
        if not isinstance(groups,dict): raise ValueError
        ids=groups.get(str(owner.id))
        if ids is None: return assigned
        if not isinstance(ids,list) or not 1<=len(ids)<=200 or any(type(i) is not int or i<=0 for i in ids): raise ValueError
        if owner.assigned_franchise_id() not in ids: raise ValueError
        selected=Franchise.query.filter(Franchise.id.in_(ids)).all()
        if {b.id for b in selected}!=set(ids): raise ValueError
    except (ValueError,TypeError):
        raise ValueError('Head Office must correct the configured Attendance holder branch group.')
    return sorted({b.id:b for b in [*assigned,*selected]}.values(),key=lambda b:b.business_name or '')
