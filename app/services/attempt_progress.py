"""Portable checkpoints; recover older paused attempts from their existing logs."""
import json
import re
from datetime import datetime
from app.time_utils import stored_now

from fastapi import HTTPException

from app import models
from app.services.testing_policy import lock_attempt


def legacy_time_left(db, attempt, now=None):
    limit = attempt.session.test.time_limit_minutes
    if not limit or not attempt.started_at:
        return None
    now = now or stored_now()  # Keep the original storage zone for naive checkpoints.
    cursor, elapsed, paused = attempt.started_at, 0, False
    events = db.query(models.EventLog).filter(
        models.EventLog.attempt_id == attempt.id,
        models.EventLog.event_type.in_((models.EventType.pause_test, models.EventType.resume_test)),
    ).order_by(models.EventLog.event_time, models.EventLog.id).all()
    for event in events:
        at = min(now, max(cursor, event.event_time))
        if event.event_type == models.EventType.pause_test and not paused:
            elapsed += (at - cursor).total_seconds()
            paused = True
            cursor = at
        elif event.event_type == models.EventType.resume_test and paused:
            paused = False
            cursor = at
    if not paused:
        # A legacy pause without its event cannot be dated precisely. Use the
        # last recorded activity rather than charging days of teacher pause.
        end = now
        if attempt.status == models.AttemptStatus.paused:
            last = db.query(models.EventLog).filter_by(attempt_id=attempt.id).order_by(
                models.EventLog.event_time.desc(), models.EventLog.id.desc()).first()
            end = max(cursor, last.event_time) if last else cursor
        elapsed += max(0, (end - cursor).total_seconds())
    return max(0, limit * 60 - int(elapsed))


def checkpoint(db, attempt):
    state = dict(attempt.progress_state or {})
    if 'time_left' not in state:
        state['time_left'] = legacy_time_left(db, attempt)
    if 'current_question_id' not in state:
        logs = db.query(models.EventLog).filter(
            models.EventLog.attempt_id == attempt.id,
            models.EventLog.event_type.in_((models.EventType.question_changed, models.EventType.question_returned)),
        ).order_by(models.EventLog.event_time.desc(), models.EventLog.id.desc())
        for event in logs:
            match = re.match(r'question_id=(\d+)', event.details or '')
            if match:
                state['current_question_id'] = int(match[1])
                break
    return state


def set_paused(db, attempt, paused, details):
    """Keep the frozen clock, status and teacher event in one transaction."""
    expected = models.AttemptStatus.in_progress if paused else models.AttemptStatus.paused
    if attempt.status != expected:
        return False
    lock_attempt(db, attempt)
    if attempt.status != expected:
        return False
    attempt.progress_state = checkpoint(db, attempt)
    attempt.status = models.AttemptStatus.paused if paused else models.AttemptStatus.in_progress
    db.add(models.EventLog(attempt_id=attempt.id,
        event_type=models.EventType.pause_test if paused else models.EventType.resume_test, details=details))
    db.commit()
    return True


def validate_checkpoint(db, attempt, body):
    if not isinstance(body, dict) or len(json.dumps(body)) > 262144:
        raise HTTPException(422, 'Невірний формат прогресу')
    assigned = {answer.question_id: answer for answer in db.query(models.StudentAnswer).filter_by(attempt_id=attempt.id)}
    assigned_keys = {str(qid) for qid in assigned}
    current = body.get('current_question_id')
    if current is not None and (type(current) is not int or current not in assigned):
        raise HTTPException(422, 'Питання не призначене цій спробі')
    limit = attempt.session.test.time_limit_minutes
    time_left = body.get('time_left')
    if (limit and time_left is None) or (time_left is not None and (
        type(time_left) is not int or not limit or not 0 <= time_left <= limit * 60
    )):
        raise HTTPException(422, 'Невірний залишок часу')
    times = body.get('question_times', {})
    qlimit = attempt.session.test.time_limit_per_question
    if not isinstance(times, dict) or any(
        str(key) not in assigned_keys or type(value) is not int
        or not qlimit or not 0 <= value <= qlimit for key, value in times.items()
    ):
        raise HTTPException(422, 'Невірний час питання')
    skipped = body.get('skipped_questions', [])
    if not isinstance(skipped, list) or any(type(qid) is not int or qid not in assigned for qid in skipped):
        raise HTTPException(422, 'Невірні пропущені питання')
    queue = body.get('question_queue', [])
    if (not isinstance(queue, list) or any(type(qid) is not int or qid not in assigned for qid in queue)
            or len(queue) != len(set(queue))):
        raise HTTPException(422, 'Невірний порядок питань')
    drafts = body.get('draft_answers', {})
    if not isinstance(drafts, dict):
        raise HTTPException(422, 'Невірні чернетки відповідей')
    from app.services.answer_service import validate_submission
    clean_drafts = {}
    for key, value in drafts.items():
        if str(key) not in assigned_keys:
            raise HTTPException(422, 'Питання не призначене цій спробі')
        answer = assigned[int(key)]
        if answer.answer_text is not None or answer.selected_options_json is not None:
            continue
        # Empty selections are useful drafts, though not final submissions.
        if value != [] and value is not None:
            text = answer.question.question_type == models.QuestionType.short_text or value == ''
            validate_submission(answer.question, {'answer_text': value if text else None,
                'selected_options': None if text else value})
        clean_drafts[str(key)] = value
    return {**(attempt.progress_state or {}), 'current_question_id': current,
        'time_left': time_left, 'question_times': times, 'skipped_questions': skipped,
        'question_queue': queue, 'draft_answers': clean_drafts}
