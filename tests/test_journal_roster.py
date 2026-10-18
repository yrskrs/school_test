"""Real FastAPI/SQLite roster exchange, entirely synthetic and isolated."""
import hashlib
import html
import json
import re
import unittest
import uuid
import test_flows as fixtures
from app import models
from app.routes.teacher import _test_to_dict


class JournalRosterAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    def roster_payload(self):
        return {'schema_version': 2, 'journal_instance_id': '11111111-1111-4111-8111-111111111111',
            'class': {'id': '22222222-2222-4222-8222-222222222222', 'name': '7-A', 'grade_level': 7, 'letter': 'A'},
            'subject': {'name': 'Синтетичний предмет'},
            'students': [{'id': '33333333-3333-4333-8333-333333333333', 'last_name': 'Коваленко', 'first_name': 'Ірина'}]}

    def key(self, owner=None):
        self.db.add(models.GradeExportGrant(teacher_id=(owner or self.owner).id,
            token_hash=hashlib.sha256(b'synthetic-roster-key').hexdigest()))
        self.db.commit()

    async def send(self, data):
        return await fixtures.http('POST', '/api/v2/journal/roster/', data, authorization='Bearer synthetic-roster-key')

    def class_options(self, page):
        select = re.search(r'<select id="class_name"[^>]*>(.*?)</select>', page, re.S)
        self.assertIsNotNone(select)
        return re.findall(r'<option value="([^"]*)"([^>]*)>', html.unescape(select.group(1)))

    async def test_new_imported_class_can_be_created_launched_and_selected(self):
        self.assertIsNone(self.owner.classes)
        self.key()
        data = self.roster_payload()
        data['class'].update(name='10-В', grade_level=10, letter='В')
        received = await self.send(data)
        self.assertEqual(received[0], 200, received[1])
        pupil_id = json.loads(received[1])['mappings'][0]['local_id']

        page = await fixtures.http('GET', '/teacher/tests/create', cookie=self.teacher_cookie)
        self.assertEqual(page[0], 200)
        self.assertIn('10-В', [value for value, _ in self.class_options(page[1])])
        created = await fixtures.http('POST', '/teacher/tests/create', {
            'title': 'Новий тест імпортованого класу', 'class_name': '10-В',
            'questions_json': json.dumps([{'question_type': 'single_choice', 'question_text': '2 + 2?',
                'options': [{'option_text': '4', 'is_correct': True}, {'option_text': '5'}]}]),
        }, cookie=self.teacher_cookie, form=True)
        self.assertEqual(created[0], 303, created[1])
        self.db.expire_all()
        test = self.db.query(models.Test).order_by(models.Test.id.desc()).first()
        edited = await fixtures.http('GET', f'/teacher/tests/{test.id}/edit', cookie=self.teacher_cookie)
        self.assertIn(('10-В', ' selected'), self.class_options(edited[1]))
        started = await fixtures.http('POST', f'/teacher/tests/{test.id}/start-session', cookie=self.teacher_cookie)
        self.assertEqual(started[0], 303, started[1])
        self.db.expire_all()
        session = self.db.query(models.TestSession).filter_by(test_id=test.id).one()
        roster = await fixtures.http('GET', f'/api/session-roster?code={session.access_code}')
        self.assertEqual(roster[0], 200)
        self.assertEqual(json.loads(roster[1])['students'], [{'id': pupil_id, 'name': 'Коваленко Ірина'}])
        login_page = await fixtures.http('GET', f'/student/login?code={session.access_code}')
        self.assertIn('Коваленко Ірина', login_page[1])
        login = await fixtures.http('POST', '/student/login', {
            'access_code': session.access_code, 'roster_student_id': pupil_id,
        }, form=True)
        self.assertEqual(login[0], 303, login[1])
        self.db.expire_all()
        attempt = self.db.query(models.StudentAttempt).filter_by(session_id=session.id).one()
        self.assertEqual(attempt.roster_student_id, pupil_id)

    async def test_class_choices_combine_profile_and_own_imports_without_duplicates(self):
        self.owner.classes = ' 5-А, 7-A, 5-А, '
        self.db.commit()
        self.key()
        self.assertEqual((await self.send(self.roster_payload()))[0], 200)
        for path in ('/teacher/tests/create', f'/teacher/tests/{self.test.id}/edit'):
            page = await fixtures.http('GET', path, cookie=self.teacher_cookie)
            self.assertEqual([value for value, _ in self.class_options(page[1])], ['', '5-А', '7-A'])
        other = await fixtures.http('GET', '/teacher/tests/create', cookie=self.other_cookie)
        self.assertNotIn('7-A', [value for value, _ in self.class_options(other[1])])

    async def test_existing_test_without_class_gets_roster_after_class_is_saved(self):
        self.test.class_name = None
        self.db.commit()
        self.key()
        data = self.roster_payload()
        data['class'].update(name='10-В', grade_level=10, letter='В')
        received = await self.send(data)
        self.assertEqual(received[0], 200, received[1])
        pupil_id = json.loads(received[1])['mappings'][0]['local_id']
        roster = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertFalse(roster['available'])
        self.assertEqual(roster['students'], [])

        page = await fixtures.http('GET', f'/teacher/tests/{self.test.id}/edit', cookie=self.teacher_cookie)
        self.assertIn('10-В', [value for value, _ in self.class_options(page[1])])
        saved = await fixtures.http('POST', f'/teacher/tests/{self.test.id}/edit', {
            'title': self.test.title, 'class_name': '10-В',
            'questions_json': json.dumps(_test_to_dict(self.test)['questions']),
        }, cookie=self.teacher_cookie, form=True)
        self.assertEqual(saved[0], 200, saved[1])
        roster = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertTrue(roster['available'])
        self.assertEqual(roster['students'], [{'id': pupil_id, 'name': 'Коваленко Ірина'}])
        page = await fixtures.http('GET', '/student/login?code=CHECK1')
        self.assertIn('Коваленко Ірина', page[1])
        self.db.refresh(self.attempt)
        self.assertIsNone(self.attempt.roster_student_id)

    async def test_edit_preserves_saved_class_missing_from_profile(self):
        page = await fixtures.http('GET', f'/teacher/tests/{self.test.id}/edit', cookie=self.teacher_cookie)
        self.assertIn(('7-A', ' selected'), self.class_options(page[1]))

    async def test_another_teachers_import_does_not_supply_same_named_test(self):
        self.key(self.other)
        imported = await self.send(self.roster_payload())
        self.assertEqual(imported[0], 200)
        pupil_id = json.loads(imported[1])['mappings'][0]['local_id']
        roster = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertFalse(roster['available'])
        self.assertEqual(roster['students'], [])
        login = await fixtures.http('POST', '/student/login', {
            'access_code': 'CHECK1', 'roster_student_id': pupil_id,
        }, form=True)
        self.assertEqual(login[0], 400)

    async def test_renamed_import_appears_in_forms_and_preserves_old_test_class(self):
        self.key()
        data = self.roster_payload()
        self.assertEqual((await self.send(data))[0], 200)
        data['class'].update(name='8-Б', grade_level=8, letter='Б')
        self.assertEqual((await self.send(data))[0], 200)
        create = await fixtures.http('GET', '/teacher/tests/create', cookie=self.teacher_cookie)
        self.assertIn('8-Б', [value for value, _ in self.class_options(create[1])])
        edit = await fixtures.http('GET', f'/teacher/tests/{self.test.id}/edit', cookie=self.teacher_cookie)
        self.assertIn(('7-A', ' selected'), self.class_options(edit[1]))
        self.assertIn('8-Б', [value for value, _ in self.class_options(edit[1])])

    async def test_copy_repeat_keeps_legacy_login_and_attempt_history(self):
        self.key(); before = self.attempt.id
        first = await self.send(self.roster_payload()); self.assertEqual(first[0], 200, first[1])
        second = await self.send(self.roster_payload()); self.assertEqual(second[0], 200, second[1]); self.assertEqual(json.loads(first[1])['mappings'], json.loads(second[1])['mappings'])
        self.assertEqual(self.db.query(models.RosterStudent).count(), 1)
        self.db.refresh(self.attempt); self.assertEqual(self.attempt.id, before); self.assertIsNone(self.attempt.roster_student_id)
        roster = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertFalse(roster['required']); self.assertTrue(roster['available'])
        self.assertEqual(roster['students'][0]['name'], 'Коваленко Ірина')

    async def test_pick_imported_pupil_or_enter_other_name(self):
        self.key(); report = json.loads((await self.send(self.roster_payload()))[1])
        pupil_id = report['mappings'][0]['local_id']
        selected = await fixtures.http('POST', '/student/login', {'student_name': 'Ков. І.', 'access_code': 'CHECK1', 'roster_student_id': pupil_id}, form=True)
        self.assertEqual(selected[0], 303, selected[1])
        attempt = self.db.query(models.StudentAttempt).filter_by(roster_student_id=pupil_id).one()
        self.assertEqual(attempt.student_name, 'Коваленко Ірина')
        # Imported rosters obey the same teacher-controlled outside-class gate.
        denied = await fixtures.http('POST', '/student/login', {'student_name': 'Позакласний Учень', 'access_code': 'CHECK1'}, form=True)
        self.assertEqual(denied[0], 403)
        self.test.allow_outside_class = True
        self.db.commit()
        other = await fixtures.http('POST', '/student/login', {'student_name': 'Позакласний Учень', 'access_code': 'CHECK1', 'other_student': '1'}, form=True)
        self.assertEqual(other[0], 303)
        self.assertEqual(self.db.query(models.StudentAttempt).filter_by(student_name='Позакласний Учень').count(), 1)

    async def test_same_name_does_not_merge_existing_id(self):
        self.key(); first = json.loads((await self.send(self.roster_payload()))[1])
        data = self.roster_payload(); data['journal_instance_id'] = str(uuid.uuid4())
        data['source_class_id'] = first['class_id']
        response = await self.send(data); self.assertEqual(response[0], 200, response[1])
        self.assertEqual(json.loads(response[1])['counts']['needs_review'], 1)
        self.assertEqual(self.db.query(models.RosterStudent).count(), 1)

    async def test_invalid_payload_does_not_create_pupils(self):
        self.key(); data = self.roster_payload()
        data['students'].append({'id': 'bad', 'last_name': 'Другий', 'first_name': 'Учень'})
        self.assertEqual((await self.send(data))[0], 400)
        self.assertEqual(self.db.query(models.RosterStudent).count(), 0)

    async def test_teacher_cannot_bind_other_teachers_class(self):
        self.key(); group = models.RosterClass(name='8-Б', grade_level=8)
        self.db.add(group); self.db.commit()
        data = self.roster_payload(); data['source_class_id'] = group.id
        self.assertEqual((await self.send(data))[0], 400)
        self.assertEqual(self.db.query(models.RosterStudent).count(), 0)

    async def test_api_stats_and_capability_are_visible_only_to_teacher(self):
        self.key(); await self.send(self.roster_payload())
        response = await fixtures.http('GET', '/api/v2/journal/roster/', authorization='Bearer synthetic-roster-key')
        self.assertIn('journal_roster_import', json.loads(response[1])['capabilities'])
        page = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        self.assertIn('Статистика API', page[1]); self.assertIn('запитів 2', page[1])
        self.assertEqual((await fixtures.http('GET', '/api/v2/journal/roster/'))[0], 401)

    async def test_renamed_class_keeps_legacy_session_roster(self):
        self.key(); data = self.roster_payload()
        before = json.loads((await self.send(data))[1])
        data['class'].update(name='8-Б', grade_level=8, letter='Б')
        after = await self.send(data); self.assertEqual(after[0], 200, after[1])
        self.assertEqual(json.loads(after[1])['class_id'], before['class_id'])
        page = await fixtures.http('GET', '/api/session-roster?code=CHECK1')
        self.assertTrue(json.loads(page[1])['available'])
        self.assertEqual(json.loads(page[1])['students'][0]['name'], 'Коваленко Ірина')

    async def test_student_login_page_renders_test_title_class_and_conditional_inputs(self):
        self.key()
        await self.send(self.roster_payload())
        status, html, _ = await fixtures.http('GET', '/student/login?code=CHECK1')
        self.assertEqual(status, 200)
        self.assertIn('Synthetic Test', html)
        self.assertIn('7-A', html)
        self.assertIn('Учень зі списку класу', html)
        self.assertIn('Коваленко Ірина', html)
        self.assertIn('Мене немає у списку — введу ім’я самостійно', html)
        self.assertIn('id="manual-name-group" style="display: none;"', html)
        # Login with empty student_name but supplied roster_student_id
        pupil = self.db.query(models.RosterStudent).first()
        res = await fixtures.http('POST', '/student/login', {'student_name': '', 'access_code': 'CHECK1', 'roster_student_id': pupil.id}, form=True)
        self.assertEqual(res[0], 303)
        attempt = self.db.query(models.StudentAttempt).filter_by(roster_student_id=pupil.id).first()
        self.assertEqual(attempt.student_name, 'Коваленко Ірина')

    async def test_api_keys_management_pause_rename_delete(self):
        import re, html
        page = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        token = re.search(r'name="csrf" value="([^"]+)"', page[1]).group(1)

        # 1. Create a named API key
        res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'simple_key', 'name': 'Мій тест-журнал 7-А', 'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(res[0], 200)
        self.assertIn('Мій тест-журнал 7-А', res[1])
        api_key = html.unescape(re.search(r'id="journal-api-key"[^>]*>(.*?)</textarea>', res[1], re.S).group(1))

        # Check key works
        grant = self.db.query(models.GradeExportGrant).filter_by(teacher_id=self.owner.id, active=True).first()
        self.assertIsNotNone(grant)
        grant_id = grant.id
        auth_header = 'Bearer ' + api_key
        check_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=auth_header)
        self.assertEqual(check_call[0], 200)

        # 2. Pause the key
        pause_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'toggle_key', 'grant_id': grant_id, 'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(pause_res[0], 200)
        self.assertIn('поставлено на паузу', pause_res[1])
        self.assertIn('На паузі', pause_res[1])

        # Requests with paused key must be rejected with 401
        blocked_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=auth_header)
        self.assertEqual(blocked_call[0], 401)

        # 3. Resume the key
        resume_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'toggle_key', 'grant_id': grant_id, 'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(resume_res[0], 200)
        self.assertIn('активовано', resume_res[1])
        unblocked_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=auth_header)
        self.assertEqual(unblocked_call[0], 200)

        # 4. Rename the key
        rename_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'rename_key', 'grant_id': grant_id, 'name': 'Нова назва журналу', 'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(rename_res[0], 200)
        self.assertIn('Нова назва журналу', rename_res[1])

        # 5. Delete the key
        delete_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'delete_key', 'grant_id': grant_id, 'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(delete_res[0], 200)
        self.assertIn('успішно видалено', delete_res[1])
        deleted_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=auth_header)
        self.assertEqual(deleted_call[0], 401)

    async def test_api_keys_description_editing_rotation_and_statistics(self):
        import re, html
        page = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        token = re.search(r'name="csrf" value="([^"]+)"', page[1]).group(1)

        # 1. Create a key with name and description
        create_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'simple_key',
            'name': 'Журнал 8-А Інформатика',
            'description': 'Основний ключ для синхронізації тестів з кабінету 12',
            'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(create_res[0], 200)
        self.assertIn('Журнал 8-А Інформатика', create_res[1])
        self.assertIn('Основний ключ для синхронізації тестів з кабінету 12', create_res[1])
        api_key = html.unescape(re.search(r'id="journal-api-key"[^>]*>(.*?)</textarea>', create_res[1], re.S).group(1))

        grant = self.db.query(models.GradeExportGrant).filter_by(teacher_id=self.owner.id, active=True).order_by(models.GradeExportGrant.id.desc()).first()
        self.assertIsNotNone(grant)
        grant_id = grant.id
        auth_header = 'Bearer ' + api_key

        # 2. Make an API call (GET roster) and verify statistics are recorded
        res1 = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=auth_header)
        self.assertEqual(res1[0], 200)

        # Check that page reflects 1 request and recent call
        page_after_call = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        self.assertIn('Запитів: <strong style="color:var(--text)">1</strong>', page_after_call[1])
        self.assertIn('Запит списків (GET)', page_after_call[1])

        # 3. Edit name and description via action="edit_key"
        edit_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'edit_key',
            'grant_id': grant_id,
            'name': 'Оновлений Журнал 8-А',
            'description': 'Оновлений опис: перенесено на домашній сервер',
            'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(edit_res[0], 200)
        self.assertIn('Оновлений Журнал 8-А', edit_res[1])
        self.assertIn('Оновлений опис: перенесено на домашній сервер', edit_res[1])

        # 4. Token rotation without deleting the grant
        rotate_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'edit_key',
            'grant_id': grant_id,
            'name': 'Оновлений Журнал 8-А',
            'description': 'Оновлений опис: перенесено на домашній сервер',
            'rotate_token': '1',
            'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(rotate_res[0], 200)
        self.assertIn('Згенеровано новий секретний ключ', rotate_res[1])
        new_api_key = html.unescape(re.search(r'id="journal-api-key"[^>]*>(.*?)</textarea>', rotate_res[1], re.S).group(1))
        self.assertNotEqual(new_api_key, api_key)

        # Old key should now return 401
        old_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=auth_header)
        self.assertEqual(old_call[0], 401)

        # New key works and request counter increases to 2
        new_auth_header = 'Bearer ' + new_api_key
        new_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=new_auth_header)
        self.assertEqual(new_call[0], 200)

        page_after_rot = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        self.assertIn('Запитів: <strong style="color:var(--text)">2</strong>', page_after_rot[1])

        # 5. Pause key and verify paused attempt tracking
        await fixtures.http('POST', '/teacher/integrations', {
            'action': 'toggle_key', 'grant_id': grant_id, 'csrf': token
        }, self.teacher_cookie, form=True)

        paused_call = await fixtures.http('GET', '/api/v2/journal/roster/', authorization=new_auth_header)
        self.assertEqual(paused_call[0], 401)

        page_after_pause = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        self.assertIn('призупинених запитів', page_after_pause[1])

        # Reactivate key
        await fixtures.http('POST', '/teacher/integrations', {
            'action': 'toggle_key', 'grant_id': grant_id, 'csrf': token
        }, self.teacher_cookie, form=True)

        # 6. Reset statistics
        reset_res = await fixtures.http('POST', '/teacher/integrations', {
            'action': 'reset_stats', 'grant_id': grant_id, 'csrf': token
        }, self.teacher_cookie, form=True)
        self.assertEqual(reset_res[0], 200)
        self.assertIn('успішно скинуто', reset_res[1])

        page_after_reset = await fixtures.http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        self.assertIn('Запитів: <strong style="color:var(--text)">0</strong>', page_after_reset[1])


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        fixtures.engine.dispose()
        fixtures.WORK.cleanup()
