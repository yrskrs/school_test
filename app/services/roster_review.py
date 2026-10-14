"""Prepare class-scoped suggestions without treating names as identities."""
import unicodedata
from collections import Counter
from copy import deepcopy

from school_sync.journal_roster import signature, similar


def normalized_name(name):
    return ' '.join(unicodedata.normalize('NFKC', name).casefold()
                    .replace('’', "'").replace('ʼ', "'").split())


def prepare_review(engine, rows, classes, students):
    groups = {group.id: group for group in classes}
    native_students = {row['native_id']: row for row in rows
                       if row['kind'] == 'student' and row.get('native_id') is not None}
    by_class = {group.id: [] for group in classes}
    for pupil in students:
        if pupil.class_id in by_class:
            by_class[pupil.class_id].append(pupil)
    reviews = []
    for record in rows:
        if record['kind'] == 'alias':
            continue
        payload = record['payload']
        class_id = record.get('native_id') if record['kind'] == 'class' else None
        if record['kind'] == 'student':
            parent = engine.store.get(engine.canonical(payload['class_ref']))
            class_id = parent.get('native_id') if parent else None
        if class_id is not None and class_id not in groups:
            continue
        review = {'record': record, 'class_id': class_id,
                  'class_name': groups[class_id].name if class_id in groups else '',
                  'blocked': record['kind'] == 'student' and class_id is None,
                  'candidates': [], 'suggested_id': None, 'exact': False,
                  'native_versions': {}}
        if record['status'] != 'ready' and not review['blocked']:
            options = classes if record['kind'] == 'class' else by_class.get(class_id, [])
            for option in options:
                if record['kind'] == 'student':
                    bound = native_students.get(option.id)
                    if (bound and bound['ref'] != record['ref']
                            and (set(bound['clock']) & set(record['clock'])) - {engine.origin}):
                        continue
                name = option.name if record['kind'] == 'class' else option.full_name()
                exact = normalized_name(name) == normalized_name(payload['name'])
                match = exact or similar(name, payload['name'])
                review['candidates'].append({'id': option.id, 'name': name,
                    'exact': exact, 'match': match, 'active': option.active})
                review['native_versions'][str(option.id)] = signature(engine.native.read(record['kind'], option.id))
            review['candidates'].sort(key=lambda item: (not item['exact'], not item['match'], item['name'], item['id']))
            exact = [item for item in review['candidates'] if item['exact']]
            matches = [item for item in review['candidates'] if item['match']]
            if len(exact) == 1:
                review.update(suggested_id=exact[0]['id'], exact=True)
            elif len(matches) == 1:
                review['suggested_id'] = matches[0]['id']
        reviews.append(review)
    # Even identical names are ambiguous when two incoming IDs claim one pupil.
    claims = Counter((item['class_id'], item['suggested_id']) for item in reviews
                     if item['record']['kind'] == 'student' and item['suggested_id'])
    batches = {}
    for item in reviews:
        record = item['record']
        if (record['kind'] == 'student' and record.get('native_id') is None
                and item['exact'] and claims[(item['class_id'], item['suggested_id'])] == 1
                and record.get('pending')
                and all(p['kind'] == 'student' and p['payload'] == record['payload'] for p in record['pending'])):
            native_id = item['suggested_id']
            batches.setdefault(item['class_id'], []).append({'ref': record['ref'],
                'native_id': native_id, 'expected': deepcopy(record),
                'native_expected': engine.payload('student', native_id)})
    return reviews, batches
