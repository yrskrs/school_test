"""Paused attempts resume across devices using synthetic, isolated data only."""
import json
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import test_flows as fixtures

from app import models
from app.services.attempt_progress import checkpoint, legacy_time_left


class AttemptProgress(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def page_config(self, cookie):
        status, body, _ = await fixtures.http('GET', f'/student/test/{self.attempt.id}', cookie=cookie)
        self.assertEqual(status, 200, body)
        return json.loads(re.search(r'id="page-config">(.*?)</script>', body, re.DOTALL).group(1))

    async def login(self, **form):
        return await fixtures.http('POST', '/student/login', {
            'access_code': self.session.access_code, 'student_name': self.attempt.student_name, **form,
        }, form=True)

    def legacy_pause(self):
        paused_at = datetime.now() - timedelta(days=3)  # noqa: DTZ005 -- Match legacy database timestamps.
        self.test.time_limit_minutes = 30
        self.test.allow_retake = False
        self.attempt.started_at = paused_at - timedelta(seconds=120)
        self.attempt.status = models.AttemptStatus.paused
        self.db.add(models.EventLog(attempt_id=self.attempt.id, event_type=models.EventType.pause_test,
            event_time=paused_at, details='Призупинено вчителем'))
        self.db.commit()
        return paused_at

    async def test_legacy_pause_three_days_old_resumes_same_attempt_from_new_browser(self):
        when = self.legacy_pause()
        qid = self.questions['short_text'].id
        self.db.add(models.EventLog(attempt_id=self.attempt.id, event_type=models.EventType.question_changed,
            event_time=when - timedelta(seconds=2), details=f'question_id={qid}; action=answer_edited'))
        self.db.commit()
        status, _, headers = await self.login(student_name='  STUDENT   EXAMPLE ')
        self.assertEqual(status, 303)
        cookie = headers[b'set-cookie'].decode().split(';', 1)[0]
        cfg = await self.page_config(cookie)
        self.assertEqual(cfg['attempt_status'], 'paused')
        self.assertEqual(cfg['time_remaining'], 1680)
        self.assertEqual(cfg['progress_state']['current_question_id'], qid)
        self.assertEqual(self.db.query(models.StudentAttempt).count(), 1)
        self.assertEqual((await fixtures.http('GET', f'/student/test/{self.attempt.id}', cookie=self.student_cookie))[0], 401)
        self.assertEqual((await fixtures.http('POST', f'/student/test/{self.attempt.id}/save-answer',
            {'question_id': qid, 'answer_text': 'Wait for teacher'}, cookie))[0], 409)
        self.assertEqual((await fixtures.http('POST', f'/teacher/attempts/{self.attempt.id}/resume', cookie=self.teacher_cookie))[0], 200)
        cfg = await self.page_config(cookie)
        self.assertEqual(cfg['time_remaining'], 1680)
        self.assertEqual(cfg['attempt_status'], 'in_progress')

    async def test_portable_checkpoint_keeps_draft_question_time_order_and_saved_answer(self):
        self.test.time_limit_minutes = 20
        self.test.time_limit_per_question = 60
        self.test.shuffle_questions = self.test.shuffle_options = True
        self.db.commit()
        cfg_before = await self.page_config(self.student_cookie)
        answered = self.questions['single_choice']
        await fixtures.http('POST', f'/student/test/{self.attempt.id}/save-answer',
            {'question_id': answered.id, 'selected_options': [answered.options[0].id]}, self.student_cookie)
        current = self.questions['short_text'].id
        body = {'current_question_id': current, 'time_left': 1087, 'question_times': {str(current): 17},
            'skipped_questions': [self.questions['matching'].id], 'draft_answers': {str(current): 'Незавершена відповідь'}}
        self.assertEqual((await fixtures.http('POST', f'/student/test/{self.attempt.id}/progress', body, self.student_cookie))[0], 200)
        await fixtures.http('POST', f'/teacher/sessions/{self.session.id}/pause-all', cookie=self.teacher_cookie)
        status, _, headers = await self.login()
        self.assertEqual(status, 303)
        cookie = headers[b'set-cookie'].decode().split(';', 1)[0]
        cfg = await self.page_config(cookie)
        self.assertEqual(cfg['time_remaining'], 1087)
        self.assertEqual(cfg['progress_state']['draft_answers'], body['draft_answers'])
        self.assertEqual(cfg['progress_state']['question_times'], body['question_times'])
        self.assertEqual(cfg['skipped_questions'], body['skipped_questions'])
        self.assertEqual(cfg['locked_questions'], [answered.id])
        self.assertEqual(cfg['test_data'], cfg_before['test_data'])
        # Previous device can no longer overwrite a transferred checkpoint.
        self.assertEqual((await fixtures.http('POST', f'/student/test/{self.attempt.id}/progress', body, self.student_cookie))[0], 401)
        self.assertEqual((await fixtures.http('POST', f'/teacher/sessions/{self.session.id}/resume-all', cookie=self.teacher_cookie))[0], 200)
        self.assertEqual((await self.page_config(cookie))['time_remaining'], 1087)
        self.assertEqual((await fixtures.http('POST', f'/student/test/{self.attempt.id}/save-answer',
            {'question_id': current, 'answer_text': 'Продовжена відповідь'}, cookie))[0], 200)

    async def test_roster_identity_instead_of_name_and_same_browser_recovery(self):
        group = models.RosterClass(name='7-A', grade_level=7, letter='A')
        self.db.add(group)
        self.db.flush()
        pupil = models.RosterStudent(class_id=group.id, first_name='Учень', last_name='Приклад')
        self.db.add(pupil)
        self.db.flush()
        self.session.roster_class_id = group.id
        self.attempt.roster_student_id = pupil.id
        self.attempt.student_name = pupil.full_name()
        self.db.commit()
        self.legacy_pause()
        original_nonce = self.attempt.auth_nonce
        form = {'access_code': self.session.access_code, 'roster_student_id': pupil.id}
        self.assertEqual((await fixtures.http('POST', '/student/login', form, self.student_cookie, form=True))[0], 303)
        self.db.refresh(self.attempt)
        self.assertEqual(self.attempt.auth_nonce, original_nonce)
        status, _, headers = await fixtures.http('POST', '/student/login', form, form=True)
        self.assertEqual(status, 303)
        cookie = headers[b'set-cookie'].decode().split(';', 1)[0]
        self.assertEqual((await self.page_config(cookie))['attempt_status'], 'paused')
        self.assertEqual(self.db.query(models.StudentAttempt).count(), 1)

    async def test_checkpoint_validation_ownership_and_terminal_attempt(self):
        path = f'/student/test/{self.attempt.id}/progress'
        qid = self.questions['short_text'].id
        body = {'current_question_id': qid, 'time_left': None, 'draft_answers': {str(qid): 'draft'}}
        self.assertEqual((await fixtures.http('POST', path, body))[0], 401)
        self.assertEqual((await fixtures.http('POST', path, body, self.student_cookie))[0], 200)
        self.db.refresh(self.attempt)
        before = dict(self.attempt.progress_state)
        for invalid in ([], {**body, 'current_question_id': True}, {**body, 'current_question_id': 99999},
                        {**body, 'question_times': {str(qid): 5}}, {**body, 'time_left': 10},
                        {**body, 'skipped_questions': [True]}, {**body, 'draft_answers': {str(qid): 'x' * 2001}}):
            self.assertEqual((await fixtures.http('POST', path, invalid, self.student_cookie))[0], 422)
            self.db.refresh(self.attempt)
            self.assertEqual(self.attempt.progress_state, before)
        await fixtures.http('POST', f'/student/test/{self.attempt.id}/finish', {}, self.student_cookie)
        self.assertEqual((await fixtures.http('POST', path, body, self.student_cookie))[0], 409)

    async def test_legacy_multiple_pause_cycles_exclude_entire_pauses(self):
        start = datetime.now() - timedelta(days=6)  # noqa: DTZ005 -- Match legacy database timestamps.
        self.test.time_limit_minutes = 10
        self.attempt.started_at = start
        self.attempt.status = models.AttemptStatus.paused
        events = [(20, models.EventType.pause_test), (86420, models.EventType.resume_test),
            (86450, models.EventType.pause_test), (172850, models.EventType.resume_test),
            (172900, models.EventType.pause_test)]
        self.db.add_all([models.EventLog(attempt_id=self.attempt.id, event_type=kind,
            event_time=start + timedelta(seconds=seconds)) for seconds, kind in events])
        self.db.commit()
        self.assertEqual(legacy_time_left(self.db, self.attempt), 500)
        self.assertEqual(checkpoint(self.db, self.attempt)['time_left'], 500)

    async def test_legacy_missing_pause_event_does_not_consume_days(self):
        self.test.time_limit_minutes = 10
        self.attempt.started_at = datetime.now() - timedelta(days=3)  # noqa: DTZ005 -- Match legacy database timestamps.
        self.attempt.status = models.AttemptStatus.paused
        self.db.commit()
        self.assertEqual(checkpoint(self.db, self.attempt)['time_left'], 600)

    async def test_closed_session_cannot_be_claimed_and_live_attempt_requires_pause(self):
        self.assertEqual((await self.login())[0], 409)
        self.legacy_pause()
        self.session.is_active = False
        self.db.commit()
        self.assertIn((await self.login())[0], (400, 403))


if __name__ == '__main__':
    unittest.main(verbosity=2)
