import json
import re
import uuid
import unittest
import hashlib
from datetime import datetime
import test_flows as fixtures
from app import crud, models
from app.security import create_teacher_session, set_student_cookie
from app.services.outside_students import browser_identity, remember, COOKIE
from starlette.requests import Request


class OutsideStudentsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    def test_signed_identity_survives_new_test_and_does_not_link_other_browser(self):
        request = Request({'type': 'http', 'headers': []})
        identity, identities = browser_identity(request, self.owner.id, 'Підмінний Учень')
        cookie = fixtures.cookie_for(remember, identities)
        second = Request({'type': 'http', 'headers': [(b'cookie', cookie.encode())]})
        self.assertEqual(browser_identity(second, self.owner.id, 'Підмінний Учень')[0], identity)
        self.assertNotEqual(browser_identity(request, self.owner.id, 'Підмінний Учень')[0], identity)
        self.assertNotEqual(browser_identity(second, self.owner.id + 1, 'Підмінний Учень')[0], identity)
        invalid = Request({'type': 'http', 'headers': [(b'cookie', (COOKIE + '=tampered').encode())]})
        self.assertNotEqual(browser_identity(invalid, self.owner.id, 'Підмінний Учень')[0], identity)

    async def test_outside_filter_and_grouping_preserve_twins_and_scope(self):
        identity = str(uuid.uuid4())
        for outside in (identity, identity, str(uuid.uuid4())):
            attempt = crud.create_attempt(self.db, self.session.id, 'Підмінний Учень', outside_student_id=outside)
            attempt.status, attempt.score, attempt.max_score = models.AttemptStatus.finished, 8, 12
        self.db.commit()
        cookie = fixtures.cookie_for(create_teacher_session, self.owner.id, self.owner.username)
        status, html, _ = await fixtures.http('GET', '/teacher/results?pupil_scope=outside', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('Підмінний Учень', html)
        self.assertIn('pupil_scope=outside', html)
        # Two equal-name identities produce two result groups; same UUID retakes share one.
        self.assertEqual(re.search(r'Згруповано \((\d+)\)', html).group(1), '2')
        status, html, _ = await fixtures.http('GET', '/teacher/results?pupil_scope=roster', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(re.search(r'Згруповано \((\d+)\)', html).group(1), '0')

    async def test_export_uses_stable_identity_and_record_id_across_retakes(self):
        group = models.RosterClass(name='7-A', grade_level=7, letter='A')
        subject = models.RosterSubject(name='Синтетичний предмет')
        self.db.add_all([group, subject])
        self.db.flush()
        self.session.roster_class_id, self.session.roster_subject_id = group.id, subject.id
        self.session.lesson_date = '2026-10-14'
        self.db.add(models.GradeExportGrant(teacher_id=self.owner.id,
            token_hash=hashlib.sha256(b'outside-key').hexdigest()))
        identity = str(uuid.uuid4())
        async def export():
            status, body, _ = await fixtures.http('GET',
                f'/api/v2/journal/grades/?class_id={group.id}&subject_id={subject.id}&date_from=2026-01-01&date_to=2026-12-31',
                authorization='Bearer outside-key')
            self.assertEqual(status, 200, body)
            return json.loads(body)['grades']
        def finished(outside):
            attempt = crud.create_attempt(self.db, self.session.id, 'Однакове Ім’я', outside_student_id=outside)
            attempt.status, attempt.score, attempt.max_score = models.AttemptStatus.finished, 8, 12
            attempt.finished_at = datetime.now()
            self.db.commit()
            return attempt
        finished(identity)
        first = (await export())[0]
        latest = finished(identity)
        second = (await export())[0]
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['student_key'], 'outside:' + identity)
        self.assertEqual(second['attempt_id'], latest.id)
        finished(str(uuid.uuid4()))
        self.assertEqual(len(await export()), 2)

    async def test_real_login_new_session_and_retake_keep_the_browser_identity(self):
        data = {'student_name': 'Підмінний Учень', 'access_code': 'CHECK1'}
        status, _, headers = await fixtures.http('POST', '/student/login', data, form=True)
        self.assertEqual(status, 303)
        outside_cookie = headers[b'set-cookie'].decode().split(';', 1)[0]
        first = self.db.query(models.StudentAttempt).filter_by(student_name='Підмінний Учень').one()
        self.assertIsNotNone(first.outside_student_id)
        crud.create_session(self.db, self.test.id, 'OUTSIDE2')
        data['access_code'] = 'OUTSIDE2'
        status, _, _ = await fixtures.http('POST', '/student/login', data, form=True, cookie=outside_cookie)
        self.assertEqual(status, 303)
        rows = self.db.query(models.StudentAttempt).filter_by(student_name='Підмінний Учень').order_by(models.StudentAttempt.id).all()
        self.assertEqual(rows[0].outside_student_id, rows[1].outside_student_id)
        first.status, first.finished_at = models.AttemptStatus.finished, datetime.now()
        self.db.commit()
        status, _, _ = await fixtures.http('POST', f'/student/test/{first.id}/retake',
            cookie=fixtures.cookie_for(set_student_cookie, first.id))
        self.assertEqual(status, 303)
        self.assertEqual(self.db.query(models.StudentAttempt).order_by(models.StudentAttempt.id.desc()).first().outside_student_id,
                         first.outside_student_id)

    async def test_native_pupil_missing_from_imported_journal_roster_is_separate(self):
        group = models.RosterClass(name='8-З', grade_level=8, letter='З')
        self.db.add(group); self.db.flush()
        known = models.RosterStudent(class_id=group.id, first_name='Учень', last_name='Відомий')
        other = models.RosterStudent(class_id=group.id, first_name='Учень', last_name='Окремий')
        self.db.add_all([known, other]); self.db.flush()
        state = self.db.get(models.RosterState, 1)
        if state is None:
            state = models.RosterState(id=1, data={}); self.db.add(state)
        state.data = {'journal_imports': {f'{self.owner.id}:synthetic': {'class_id': group.id,
            'students': {'known': {'native_id': known.id}}}}}
        self.db.commit()
        for pupil in (known, other):
            attempt = crud.create_attempt(self.db, self.session.id, pupil.full_name(), roster_student_id=pupil.id)
            attempt.status, attempt.score, attempt.max_score = models.AttemptStatus.finished, 8, 12
        self.db.commit()
        cookie = fixtures.cookie_for(create_teacher_session, self.owner.id, self.owner.username)
        status, html, _ = await fixtures.http('GET', '/teacher/results?pupil_scope=outside', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(re.search(r'Згруповано \((\d+)\)', html).group(1), '1')
