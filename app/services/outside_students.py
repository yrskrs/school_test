"""A signed browser identity; a typed name never links different browsers."""
import hashlib
import json
from uuid import UUID, uuid4
from app.security import _sign, _verify_sign

COOKIE = 'schooltest_outside_students'


def browser_identity(request, teacher_id, name):
    key = hashlib.sha256(f'{teacher_id}:{" ".join(name.split()).casefold()}'.encode()).hexdigest()
    try:
        identities = json.loads(_verify_sign(request.cookies.get(COOKIE, '')) or '{}')
        if not isinstance(identities, dict) or len(identities) > 20:
            identities = {}
        identity = str(UUID(identities[key])) if key in identities else str(uuid4())
    except (ValueError, TypeError, AttributeError):
        identities, identity = {}, str(uuid4())
    identities[key] = identity
    return identity, dict(list(identities.items())[-20:])


def remember(response, identities):
    response.set_cookie(COOKIE, _sign(json.dumps(identities, separators=(',', ':'))),
        max_age=60 * 60 * 24 * 365, httponly=True, samesite='lax')


def outside_predicate(db, teacher_id):
    from app import models
    state = db.get(models.RosterState, 1)
    bindings = [item for key, item in ((state.data or {}) if state else {}).get('journal_imports', {}).items()
                if key.startswith(str(teacher_id) + ':')]
    known = {entry['native_id'] for item in bindings
             if not (item.get('remote_class', {}).get('letter') == 'ПК'
                     and item.get('remote_class', {}).get('name', '').endswith(' (Позакласні)'))
             for entry in item.get('students', {}).values()}
    return lambda attempt: attempt.roster_student_id is None or (bool(bindings) and attempt.roster_student_id not in known)


def outside_summary(db, teacher_id):
    from app import crud
    is_outside = outside_predicate(db, teacher_id)
    groups = {}
    active, archived = 0, 0
    for archive in (False, True):
        for attempt in crud.get_all_finished_attempts(db, teacher_id, is_archived=archive):
            if not is_outside(attempt):
                continue
            if archive:
                archived += 1
            else:
                active += 1
            identity = ('roster', attempt.roster_student_id) if attempt.roster_student_id else ('outside', attempt.outside_student_id) if attempt.outside_student_id else ('legacy', attempt.id)
            group = groups.setdefault(identity, {'name': attempt.student_name, 'active': 0, 'archived': 0})
            group['archived' if archive else 'active'] += 1
    return {'count': len(groups), 'active': active, 'archived': archived,
            'students': sorted(groups.values(), key=lambda item: item['name'])[:20]}
