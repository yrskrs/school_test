"""Attempt limits and outside-class access against isolated real HTTP/SQLite flows."""
import asyncio
import json
import unittest

import test_flows as fixtures
from app import crud, models
from app.routes.teacher import _test_to_dict
from app.security import set_student_cookie
from app.services.import_export_service import import_test_from_json
from app.services.test_file_service import test_to_dict


class TestAccess(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown
    bind_roster = fixtures.Flows.bind_roster

    def set_limit(self, limit):
        self.test.max_attempts = limit
        self.test.allow_retake = limit is None or limit > 1
        self.db.commit()

    async def login(self, name=None, cookie=None, **extra):
        return await fixtures.http('POST', '/student/login', {
            'student_name': name or self.attempt.student_name,
            'access_code': self.session.access_code, **extra,
        }, cookie=cookie, form=True)

    async def finish(self, attempt, status=models.AttemptStatus.finished):
        attempt.status = status
        attempt.score = 0
        attempt.max_score = 1
        self.db.commit()

    async def test_teacher_saves_numeric_unlimited_and_single_attempt_settings(self):
        page = await fixtures.http('GET', '/teacher/tests/create', cookie=self.teacher_cookie)
        self.assertIn('name="max_attempts"', page[1])
        self.assertIn('name="allow_outside_class"', page[1])
        created = await fixtures.http('POST', '/teacher/tests/create', {
            'title': 'Access settings', 'class_name': '7-A', 'attempt_limit_mode': 'limited',
            'max_attempts': '3', 'allow_outside_class': 'true',
        }, cookie=self.teacher_cookie, form=True)
        self.assertEqual(created[0], 303, created[1])
        self.db.expire_all()
        test = self.db.query(models.Test).order_by(models.Test.id.desc()).first()
        self.assertEqual(test.attempt_limit, 3)
        self.assertTrue(test.allow_retake)
        self.assertTrue(test.allow_outside_class)
        page = await fixtures.http('GET', f'/teacher/tests/{test.id}/edit', cookie=self.teacher_cookie)
        self.assertRegex(page[1], r'name="max_attempts"[^>]*value="3"')
        self.assertRegex(page[1], r'name="allow_outside_class"[^>]*checked')
        for mode, count, expected in [('unlimited', None, None), ('limited', 1, 1)]:
            data = {'title': test.title, 'attempt_limit_mode': mode}
            if count is not None:
                data['max_attempts'] = count
            saved = await fixtures.http('POST', f'/teacher/tests/{test.id}/edit', data,
                                        cookie=self.teacher_cookie, form=True)
            self.assertEqual(saved[0], 200, saved[1])
            self.db.refresh(test)
            self.assertEqual(test.attempt_limit, expected)
            self.assertEqual(test.allow_retake, expected is None)
            self.assertFalse(test.allow_outside_class)

    async def test_invalid_limits_never_save(self):
        before = self.db.query(models.Test).count()
        invalid = [
            {'attempt_limit_mode': 'limited'},
            {'attempt_limit_mode': 'invalid', 'max_attempts': 2},
            *[{'attempt_limit_mode': 'limited', 'max_attempts': value}
              for value in (0, -1, '1.5', 'bad', 2147483648)],
        ]
        for fields in invalid:
            response = await fixtures.http('POST', '/teacher/tests/create', {'title': 'Invalid', **fields},
                                           cookie=self.teacher_cookie, form=True)
            self.assertEqual(response[0], 422, fields)
        self.assertEqual(self.db.query(models.Test).count(), before)

    async def test_limit_blocks_login_retake_and_button_but_allows_resume(self):
        self.set_limit(2)
        self.assertEqual((await self.login(cookie=self.student_cookie))[0], 303)
        self.assertEqual(len(crud.get_attempts_by_session(self.db, self.session.id)), 1)
        await self.finish(self.attempt)
        page = await fixtures.http('GET', f'/student/test/{self.attempt.id}/finished', cookie=self.student_cookie)
        self.assertIn('Повторно пройти тест', page[1])
        retake = await fixtures.http('POST', f'/student/test/{self.attempt.id}/retake', cookie=self.student_cookie)
        self.assertEqual(retake[0], 303, retake[1])
        self.db.expire_all()
        second = self.db.query(models.StudentAttempt).order_by(models.StudentAttempt.id.desc()).first()
        cookie = fixtures.cookie_for(set_student_cookie, second.id)
        self.assertEqual((await self.login(name='  STUDENT   EXAMPLE  ', cookie=cookie))[0], 303)
        await self.finish(second, models.AttemptStatus.timeout)
        # Archiving results must not reset a student's limit.
        self.attempt.is_archived = True
        self.db.commit()
        self.assertEqual((await self.login(name='student example'))[0], 403)
        self.assertEqual((await fixtures.http('POST', f'/student/test/{second.id}/retake', cookie=cookie))[0], 403)
        page = await fixtures.http('GET', f'/student/test/{second.id}/finished', cookie=cookie)
        self.assertNotIn('Повторно пройти тест', page[1])
        self.assertIn('Використано проходжень: 2 з 2', page[1])
        self.assertEqual(len(crud.get_attempts_by_session(self.db, self.session.id)), 2)
        # Limits belong to each pupil and each session independently.
        self.assertEqual((await self.login(name='Other Pupil'))[0], 303)
        another = crud.create_session(self.db, self.test.id, 'OTHER1')
        response = await fixtures.http('POST', '/student/login', {
            'student_name': self.attempt.student_name, 'access_code': another.access_code,
        }, form=True)
        self.assertEqual(response[0], 303)

    async def test_one_attempt_counts_stopped_and_allows_in_progress_resume(self):
        self.set_limit(1)
        self.assertEqual((await self.login(cookie=self.student_cookie))[0], 303)
        await self.finish(self.attempt, models.AttemptStatus.stopped)
        self.assertEqual((await self.login())[0], 403)
        self.assertEqual((await fixtures.http('POST', f'/student/test/{self.attempt.id}/retake', cookie=self.student_cookie))[0], 403)

    async def test_unlimited_still_prevents_duplicate_unfinished_attempts(self):
        self.set_limit(None)
        current = self.attempt
        for _ in range(4):
            await self.finish(current)
            response = await self.login()
            self.assertEqual(response[0], 303, response[1])
            self.db.expire_all()
            current = self.db.query(models.StudentAttempt).order_by(models.StudentAttempt.id.desc()).first()
        self.assertEqual(len(crud.get_attempts_by_session(self.db, self.session.id)), 5)
        # Replaying an older retake form cannot create a parallel attempt.
        self.assertEqual((await fixtures.http('POST', f'/student/test/{self.attempt.id}/retake', cookie=self.student_cookie))[0], 409)
        self.assertEqual((await self.login())[0], 409)

    async def test_competing_login_and_retake_cannot_exceed_limit(self):
        self.set_limit(2)
        await self.finish(self.attempt)
        responses = await asyncio.gather(
            self.login(),
            fixtures.http('POST', f'/student/test/{self.attempt.id}/retake', cookie=self.student_cookie),
        )
        self.assertEqual(sorted(response[0] for response in responses), [303, 403])
        self.db.expire_all()
        self.assertEqual(len(crud.get_attempts_by_session(self.db, self.session.id)), 2)

    async def test_roster_ids_have_separate_limits_even_with_identical_names(self):
        _, _, pupils = self.bind_roster()
        self.set_limit(1)
        blocked = await self.login(roster_student_id=pupils[0].id)
        self.assertEqual(blocked[0], 403)
        allowed = await self.login(roster_student_id=pupils[1].id)
        self.assertEqual(allowed[0], 303, allowed[1])
        self.db.expire_all()
        self.assertEqual(self.db.query(models.StudentAttempt).filter_by(roster_student_id=pupils[1].id).count(), 1)

    async def test_outside_class_toggle_hides_and_enforces_manual_login(self):
        _, _, pupils = self.bind_roster()
        before = self.db.query(models.StudentAttempt).count()
        data = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertFalse(data['allow_outside_class'])
        page = await fixtures.http('GET', '/student/login?code=CHECK1')
        self.assertIn('id="other-student-toggle-wrap" style="display: none;"', page[1])
        for extra in ({'other_student': '1'}, {}):
            denied = await self.login(name='Outside Pupil', **extra)
            self.assertEqual(denied[0], 403)
            self.assertIn('Учитель не дозволив', denied[1])
        self.assertEqual(self.db.query(models.StudentAttempt).count(), before)
        self.assertEqual((await self.login(roster_student_id=pupils[1].id))[0], 303)
        self.test.allow_outside_class = True
        self.db.commit()
        page = await fixtures.http('GET', '/student/login?code=CHECK1')
        self.assertNotIn('id="other-student-toggle-wrap" style="display: none;"', page[1])
        self.assertTrue(json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])['allow_outside_class'])
        self.assertEqual((await self.login(name='Outside Pupil', other_student='1'))[0], 303)
        self.db.expire_all()
        outside = self.db.query(models.StudentAttempt).filter_by(student_name='Outside Pupil').one()
        self.assertIsNone(outside.roster_student_id)
        await self.finish(outside)
        self.test.allow_outside_class = False
        self.db.commit()
        cookie = fixtures.cookie_for(set_student_cookie, outside.id)
        self.assertEqual((await fixtures.http('POST', f'/student/test/{outside.id}/retake', cookie=cookie))[0], 403)

    async def test_no_roster_keeps_normal_manual_login(self):
        self.assertFalse(self.test.allow_outside_class)
        self.assertEqual((await self.login(name='Manual Pupil'))[0], 303)

    async def test_settings_round_trip_and_legacy_imports(self):
        self.set_limit(3)
        self.test.allow_outside_class = True
        self.db.commit()
        for serialize in (_test_to_dict, test_to_dict):
            copied = import_test_from_json(self.db, self.owner.id, json.dumps(serialize(self.test)))
            self.assertEqual(copied.attempt_limit, 3)
            self.assertTrue(copied.allow_outside_class)
        for retakes in (False, True):
            copied = import_test_from_json(self.db, self.owner.id, json.dumps({'title': 'Legacy', 'allow_retake': retakes}))
            self.assertEqual(copied.attempt_limit, None if retakes else 1)
            self.assertFalse(copied.allow_outside_class)
        for value in (0, -1, 1.5, '2', True, 2147483648):
            with self.assertRaises(ValueError):
                import_test_from_json(self.db, self.owner.id, json.dumps({'title': 'Invalid', 'max_attempts': value}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
