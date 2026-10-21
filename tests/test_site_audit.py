"""Roster and editing regressions on an isolated synthetic database."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
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
from app import crud
from app.routes.teacher import _test_to_dict
from app.services import test_service


class SiteAudit(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def test_teacher_entry_addresses_redirect_to_dashboard(self):
        for path in ('/teacher', '/teacher/'):
            response = await fixtures.http('GET', path, cookie=self.teacher_cookie)
            self.assertEqual(response[0], 303)
            self.assertEqual(response[2][b'location'], b'/teacher/dashboard')

    async def test_local_roster_from_teacher_ui_is_available_in_forms_and_login(self):
        page = await fixtures.http('GET', '/teacher/roster-sync', cookie=self.teacher_cookie)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page[1]).group(1)
        for fields in ({'action': 'class', 'name': '10-В', 'grade': '10', 'letter': 'В', 'active': '1'},):
            saved = await fixtures.http('POST', '/teacher/roster-sync', {'csrf': csrf, **fields}, self.teacher_cookie, form=True)
            self.assertEqual(saved[0], 200)
        group = self.db.query(models.RosterClass).filter_by(name='10-В').one()
        saved = await fixtures.http('POST', '/teacher/roster-sync', {
            'csrf': csrf, 'action': 'student', 'class_id': group.id,
            'first': 'Ірина', 'last': 'Синтетична', 'active': '1',
        }, self.teacher_cookie, form=True)
        self.assertEqual(saved[0], 200)
        page = await fixtures.http('GET', '/teacher/tests/create', cookie=self.teacher_cookie)
        self.assertIn('<option value="10-В">', page[1])
        self.test.class_name = '10-В'
        self.db.commit()
        data = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertEqual([p['name'] for p in data['students']], ['Синтетична Ірина'])
        login = await fixtures.http('POST', '/student/login', {
            'access_code': 'CHECK1', 'roster_student_id': data['students'][0]['id'],
        }, form=True)
        self.assertEqual(login[0], 303, login[1])

    async def test_invalid_explicit_code_does_not_open_a_pinned_test(self):
        self.session.is_pinned = True
        self.db.commit()
        page = await fixtures.http('GET', '/student/login?code=INVALID')
        self.assertNotIn('value="CHECK1"', page[1])
        self.assertIn('Невірний код доступу або сесія завершена', page[1])
        self.assertIn('type="text"\n        id="access_code"', page[1])
        normal = await fixtures.http('GET', '/student/login')
        self.assertIn('Ввести інший код тесту', normal[1])
        manual = await fixtures.http('GET', '/student/login?manual=1')
        self.assertNotIn('value="CHECK1"', manual[1])
        self.assertIn('type="text"\n        id="access_code"', manual[1])

    async def test_class_settings_save_without_questions_preserves_assigned_answers(self):
        before = [(q.id, [o.id for o in q.options]) for q in self.test.questions]
        answers = [(a.id, a.question_id) for a in self.attempt.answers]
        saved = await fixtures.http('POST', f'/teacher/tests/{self.test.id}/edit', {
            'title': self.test.title, 'class_name': '10-В',
        }, self.teacher_cookie, form=True)
        self.assertEqual(saved[0], 200, saved[1])
        self.db.expire_all()
        self.assertEqual([(q.id, [o.id for o in q.options]) for q in self.test.questions], before)
        self.assertEqual([(a.id, a.question_id) for a in self.attempt.answers], answers)

    async def test_deleting_assigned_questions_does_not_destroy_attempt_history(self):
        before = self.db.query(models.StudentAnswer).count()
        saved = await fixtures.http('POST', f'/teacher/tests/{self.test.id}/edit', {
            'title': self.test.title, 'questions_json': '[]',
        }, self.teacher_cookie, form=True)
        self.assertEqual(saved[0], 409)
        self.assertIn('використані у спробах', saved[1])
        self.assertEqual(self.db.query(models.StudentAnswer).count(), before)

    async def test_launch_error_is_visible_in_editor(self):
        from urllib.parse import urlsplit
        self.questions['single_choice'].options[0].is_correct = False
        self.db.commit()
        launched = await fixtures.http('POST', f'/teacher/tests/{self.test.id}/start-session', cookie=self.teacher_cookie)
        self.assertEqual(launched[0], 303)
        location = launched[2][b'location'].decode()
        self.assertEqual(urlsplit(location).path, f'/teacher/tests/{self.test.id}/edit')
        page = await fixtures.http('GET', location, cookie=self.teacher_cookie)
        self.assertIn('не має правильного варіанту', page[1])

    async def test_single_choice_with_two_correct_options_is_not_launched(self):
        self.questions['single_choice'].options[1].is_correct = True
        self.db.commit()
        self.assertIsNotNone(test_service.validate_test_for_launch(self.test))

    async def test_launch_rejects_invalid_definitions_for_all_eight_question_types(self):
        cases = {
            'single_choice': [{'option_text': 'A', 'is_correct': True}, {'option_text': 'B', 'is_correct': True}],
            'multiple_choice': [{'option_text': 'A', 'is_correct': False}],
            'true_false': [{'option_text': 'True', 'is_correct': True}, {'option_text': 'False', 'is_correct': True}],
            'image_choice': [{'option_text': 'A', 'is_correct': False}],
            'short_text': [{'option_text': '', 'is_correct': True}],
            'matching': [{'option_text': 'A', 'matching_text': ''}],
            'sequence': [{'option_text': 'Only one'}],
            'hotspot': [{'option_text': 'invalid region', 'is_correct': True}],
        }
        for kind, options in cases.items():
            with self.subTest(kind=kind):
                question = models.Question(question_type=models.QuestionType(kind), question_text='Synthetic question',
                    options=[models.AnswerOption(**option) for option in options])
                test = models.Test(questions=[question])
                self.assertIsNotNone(test_service.validate_test_for_launch(test))

    async def test_all_excluded_topics_cannot_start_an_empty_attempt(self):
        self.test.excluded_topics = json.dumps([''])
        self.db.commit()
        self.assertIsNotNone(test_service.validate_test_for_launch(self.test))

    async def test_edit_cannot_change_options_already_assigned_to_students(self):
        payload = _test_to_dict(self.test)['questions']
        payload[0]['options'].pop(0)
        response = await fixtures.http('POST', f'/teacher/tests/{self.test.id}/edit', {
            'title': self.test.title, 'questions_json': json.dumps(payload),
        }, self.teacher_cookie, form=True)
        self.assertEqual(response[0], 409)
        self.assertEqual(len(self.questions['single_choice'].options), 2)

    async def test_malformed_editor_payloads_and_time_limits_are_rejected(self):
        for fields in ({'questions_json': '{}'}, {'questions_json': '[{"question_type":"invalid"}]'},
                       {'time_limit_minutes': 'bad'}, {'time_limit_minutes': '-5'},
                       {'max_grade': '0'}, {'random_questions_limit': '-2'}):
            with self.subTest(fields=fields):
                before = self.db.query(models.Test).count()
                response = await fixtures.http('POST', '/teacher/tests/create', {
                    'title': 'Malformed synthetic test', **fields,
                }, self.teacher_cookie, form=True)
                self.assertEqual(response[0], 422)
                self.assertEqual(self.db.query(models.Test).count(), before)

    async def test_launched_local_roster_stays_attached_after_test_class_changes(self):
        group = models.RosterClass(name='7-A', grade_level=7)
        self.db.add(group); self.db.flush()
        self.db.add(models.RosterStudent(class_id=group.id, first_name='First', last_name='Synthetic'))
        self.db.commit()
        launched = await fixtures.http('POST', f'/teacher/tests/{self.test.id}/start-session', cookie=self.teacher_cookie)
        self.assertEqual(launched[0], 303)
        self.db.expire_all()
        session = self.db.query(models.TestSession).order_by(models.TestSession.id.desc()).first()
        self.assertEqual(session.roster_class_id, group.id)
        self.test.class_name = '10-В'
        self.db.commit()
        data = json.loads((await fixtures.http('GET', f'/api/session-roster?code={session.access_code}'))[1])
        self.assertEqual(data['class_name'], '7-A')
        self.assertEqual(len(data['students']), 1)

    async def test_inactive_and_missing_rosters_do_not_allow_manual_bypass(self):
        group = models.RosterClass(name='7-A', grade_level=7, active=False)
        self.db.add(group); self.db.flush()
        self.db.add(models.RosterStudent(class_id=group.id, first_name='First', last_name='Synthetic'))
        self.test.allow_outside_class = True
        self.db.commit()
        for group_id, expected in ((None, 'Цей клас неактивний'), (group.id, 'Цей клас неактивний')):
            self.session.roster_class_id = group_id
            self.db.commit()
            response = await fixtures.http('GET', '/api/session-roster?code=CHECK1')
            self.assertEqual(response[0], 200)
            self.assertIn(expected, json.loads(response[1])['error'])
            login = await fixtures.http('POST', '/student/login', {
                'access_code': 'CHECK1', 'student_name': 'Manual', 'other_student': '1',
            }, form=True)
            self.assertEqual(login[0], 403)

        # A missing legacy class is an invalid FK on PostgreSQL. Verify the
        # resolver and its HTTP handling without inserting corrupt records.
        from app.services.roster_service import resolve_roster_class
        missing, error = resolve_roster_class(self.db, self.test, 99999)
        self.assertIsNone(missing)
        self.assertIn('Список класу цієї сесії недоступний', error)
        with patch('app.services.roster_service.resolve_roster_class', return_value=(missing, error)):
            response = await fixtures.http('GET', '/api/session-roster?code=CHECK1')
            self.assertEqual(response[0], 200)
            self.assertEqual(json.loads(response[1])['error'], error)
            login = await fixtures.http('POST', '/student/login', {
                'access_code': 'CHECK1', 'student_name': 'Manual', 'other_student': '1',
            }, form=True)
            self.assertEqual(login[0], 403)

    async def test_wrong_pupil_id_returns_login_form_without_creating_attempt(self):
        group = models.RosterClass(name='7-A', grade_level=7)
        other_group = models.RosterClass(name='8-Б', grade_level=8)
        self.db.add_all([group, other_group]); self.db.flush()
        self.db.add(models.RosterStudent(class_id=group.id, first_name='Right', last_name='Synthetic'))
        other = models.RosterStudent(class_id=other_group.id, first_name='Wrong', last_name='Synthetic')
        self.db.add(other); self.db.commit()
        before = self.db.query(models.StudentAttempt).count()
        login = await fixtures.http('POST', '/student/login', {
            'access_code': 'CHECK1', 'roster_student_id': other.id,
        }, form=True)
        self.assertEqual(login[0], 400)
        self.assertIn('Оберіть учня зі списку саме цього класу', login[1])
        self.assertIn('id="student-login-form"', login[1])
        self.assertEqual(self.db.query(models.StudentAttempt).count(), before)

    async def test_parallel_roster_operations_preserve_both_metadata_changes(self):
        from app.services.roster_backend import operate
        operate(lambda engine, store: store.meta.update(audit_counter=0), self.db)
        barrier = threading.Barrier(2)
        def change(_):
            barrier.wait(timeout=5)
            def apply(engine, store):
                value = store.meta['audit_counter']
                time.sleep(.05)
                store.meta['audit_counter'] = value + 1
            operate(apply)
        def run():
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(change, range(2)))
        # The worker code uses only SQL, so join it directly. This avoids
        # platform-specific asyncio default-executor shutdown wakeups.
        run()
        self.db.expire_all()
        self.assertEqual(self.db.get(models.RosterState, 1).data['audit_counter'], 2)

    async def test_teacher_delete_removes_keys_and_keeps_rosters_without_inheritable_bindings(self):
        import hashlib
        from app.services.roster_backend import operate
        self.owner.username = 'admin'
        self.db.commit()
        from app.security import create_teacher_session
        self.teacher_cookie = fixtures.cookie_for(create_teacher_session, self.owner.id, self.owner.username)
        target_id = self.other.id
        test = crud.create_test(self.db, target_id, {'title': 'Synthetic deletion test'})
        grant = models.GradeExportGrant(teacher_id=target_id, token_hash=hashlib.sha256(b'delete-audit').hexdigest())
        group = models.RosterClass(name='9-X', grade_level=9)
        self.db.add_all([grant, group]); self.db.flush()
        pupil = models.RosterStudent(class_id=group.id, first_name='First', last_name='Synthetic')
        self.db.add(pupil); self.db.commit()
        grant_id, group_id, test_id = grant.id, group.id, test.id
        operate(lambda engine, store: store.meta.update(journal_imports={
            f'{target_id}:synthetic:class': {'class_id': group_id, 'students': {}, 'class_names': ['9-X']},
        }, api_grants_meta={str(grant_id): {'name': 'Deleted key'}}, journal_api_stats={str(grant_id): {'requests': 1}}), self.db)
        response = await fixtures.http('POST', '/teacher/profile/delete-teacher', {
            'username': self.other.username,
        }, self.teacher_cookie, form=True)
        self.assertEqual(json.loads(response[1])['status'], 'ok')
        self.db.expire_all()
        self.assertEqual(self.db.query(models.GradeExportGrant).filter_by(teacher_id=target_id).count(), 0)
        self.assertIsNone(self.db.get(models.Test, test_id))
        self.assertIsNotNone(self.db.get(models.RosterClass, group_id))
        self.assertEqual(self.db.query(models.RosterStudent).filter_by(class_id=group_id).count(), 1)
        state = self.db.get(models.RosterState, 1).data
        self.assertFalse(any(key.startswith(str(target_id) + ':') for key in state['journal_imports']))
        self.assertNotIn(str(grant_id), state.get('journal_api_stats', {}))
        # SQLite may reuse the highest deleted primary key. The new account
        # must not inherit the removed account's API token or journal binding.
        replacement = crud.create_teacher(self.db, 'replacement', 'Synthetic Replacement', 'synthetic-password')
        replacement.id = target_id
        self.db.commit()
        self.assertEqual(replacement.id, target_id)
        rejected = await fixtures.http('GET', '/api/v2/journal/roster/', authorization='Bearer delete-audit')
        self.assertEqual(rejected[0], 401)

    async def test_old_teacher_cookie_cannot_authenticate_recreated_same_named_account(self):
        old_id, username = self.other.id, self.other.username
        self.db.delete(self.other); self.db.commit()
        replacement = crud.create_teacher(self.db, username, 'New Synthetic Owner', 'new-synthetic-password')
        replacement.id = old_id
        self.db.commit()
        self.assertEqual(replacement.id, old_id)
        response = await fixtures.http('GET', '/teacher/tests', cookie=self.other_cookie)
        self.assertEqual(response[0], 401)

    async def test_old_student_cookie_cannot_open_a_reused_attempt_id(self):
        old_id = self.attempt.id
        self.db.delete(self.attempt); self.db.commit()
        replacement = crud.create_attempt(self.db, self.session.id, 'New Synthetic Pupil')
        replacement.id = old_id
        self.db.commit()
        self.assertEqual(replacement.id, old_id)
        response = await fixtures.http('GET', f'/student/instruction/{replacement.id}', cookie=self.student_cookie)
        self.assertEqual(response[0], 401)

    async def test_existing_legacy_cookies_continue_working_after_additive_upgrade(self):
        import base64
        from app.security import _sign
        self.owner.auth_nonce = None
        self.attempt.auth_nonce = None
        self.db.commit()
        payload = base64.b64encode(json.dumps({'id': self.owner.id, 'u': self.owner.username, 't': int(time.time())}).encode()).decode()
        teacher_cookie = 'teacher_session=' + _sign(payload)
        student_cookie = 'student_attempt=' + _sign(str(self.attempt.id))
        self.assertEqual((await fixtures.http('GET', '/teacher/tests', cookie=teacher_cookie))[0], 200)
        self.assertEqual((await fixtures.http('GET', f'/student/instruction/{self.attempt.id}', cookie=student_cookie))[0], 200)


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        fixtures.engine.dispose()
        fixtures.WORK.cleanup()
