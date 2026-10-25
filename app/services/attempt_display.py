from app.time_utils import timestamp_iso
"""Stable display identities and numbering, without changing stored attempts."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime


def student_identity(attempt):
    if attempt.roster_student_id:
        return ('roster', attempt.roster_student_id)
    if attempt.outside_student_id:
        return ('outside', attempt.outside_student_id)
    # Admission of old manual entries used this same session/name boundary.
    # Never infer a link to a roster ID or another browser from an equal name.
    return ('legacy', attempt.session_id, ' '.join(attempt.student_name.split()).casefold())


def annotate_session_attempts(attempts):
    groups = defaultdict(list)
    for attempt in attempts:
        groups[student_identity(attempt)].append(attempt)
    for identity, rows in groups.items():
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        for number, attempt in enumerate(sorted(rows, key=lambda a: (a.started_at or datetime.min, a.id)), 1):
            attempt.student_key = key
            attempt.session_attempt_number = number
            attempt.session_attempt_count = len(rows)


def monitor_attempt_data(attempts, violation_counts):
    annotate_session_attempts(attempts)
    return [{
        'id': a.id, 'student_name': a.student_name, 'student_key': a.student_key,
        'session_attempt_number': a.session_attempt_number,
        'session_attempt_count': a.session_attempt_count,
        'allow_device_transfer': bool((a.progress_state or {}).get('allow_device_transfer')),
        'violation_count': violation_counts.get(a.id, 0), 'status': a.status.value,
        'score': a.score, 'max_score': a.max_score,
        'started_at': timestamp_iso(a.started_at),
        'finished_at': timestamp_iso(a.finished_at),
        'assigned_questions': [{'question_id': answer.question_id, 'is_correct': answer.is_correct}
            for answer in sorted((ans for ans in a.answers if ans.question), key=lambda ans: ans.question.order_index)],
    } for a in attempts]
