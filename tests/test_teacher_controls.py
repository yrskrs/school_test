"""Teacher controls received in 1.6.12: real HTTP with synthetic data."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'tests'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import test_flows as fixtures
from app import crud, models


class TeacherControls(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def control(self, attempt, action, cookie=None):
        return await fixtures.http('POST', f'/teacher/attempts/{attempt.id}/{action}',
                                   cookie=cookie or self.teacher_cookie)

    async def test_pause_and_resume_registration_keeps_it_unstarted(self):
        attempt = crud.create_attempt(self.db, self.session.id, 'Not started')
        self.assertEqual((await self.control(attempt, 'pause'))[0], 200)
        self.db.refresh(attempt)
        self.assertEqual(attempt.status, models.AttemptStatus.paused)
        self.assertEqual((await self.control(attempt, 'resume'))[0], 200)
        self.db.refresh(attempt)
        self.assertEqual(attempt.status, models.AttemptStatus.not_started)
        self.assertEqual(len(attempt.answers), 0)

    async def test_transfer_pauses_active_attempt_and_allows_reentry(self):
        self.attempt.progress_state = {'time_left': 123, 'draft_answers': {'1': 'local'}, 'generation': 2}
        self.db.commit()
        old_nonce = self.attempt.auth_nonce
        response = await self.control(self.attempt, 'allow-device-transfer')
        self.assertEqual(response[0], 200)
        self.db.refresh(self.attempt)
        self.assertEqual(self.attempt.status, models.AttemptStatus.paused)
        self.assertEqual(self.attempt.progress_state['time_left'], 123)
        self.assertEqual(self.attempt.progress_state['draft_answers'], {'1': 'local'})
        self.assertNotEqual(self.attempt.auth_nonce, old_nonce)
        login = await fixtures.http('POST', '/student/login', {
            'access_code': self.session.access_code, 'student_name': self.attempt.student_name,
        }, form=True)
        self.assertEqual(login[0], 303)
        self.db.refresh(self.attempt)
        self.assertFalse(self.attempt.progress_state.get('allow_device_transfer'))
        self.assertEqual(len(crud.get_attempts_by_session(self.db, self.session.id)), 1)
        self.assertEqual((await fixtures.http('GET', f'/student/test/{self.attempt.id}', cookie=self.student_cookie))[0], 401)

    async def test_other_teacher_cannot_transfer_or_delete(self):
        for action in ('allow-device-transfer', 'delete', 'pause', 'resume'):
            self.assertEqual((await self.control(self.attempt, action, self.other_cookie))[0], 404)
        self.assertIsNotNone(self.db.get(models.StudentAttempt, self.attempt.id))


if __name__ == '__main__':
    unittest.main()
