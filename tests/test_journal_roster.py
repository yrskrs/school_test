"""Real FastAPI/SQLite roster exchange, entirely synthetic and isolated."""
import hashlib
import json
import unittest
import uuid
import test_flows as fixtures
from app import models


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

    async def test_copy_repeat_keeps_legacy_login_and_attempt_history(self):
        self.key(); before = self.attempt.id
        first = await self.send(self.roster_payload()); self.assertEqual(first[0], 200, first[1])
        second = await self.send(self.roster_payload()); self.assertEqual(json.loads(first[1])['mappings'], json.loads(second[1])['mappings'])
        self.assertEqual(self.db.query(models.RosterStudent).count(), 1)
        self.db.refresh(self.attempt); self.assertEqual(self.attempt.id, before); self.assertIsNone(self.attempt.roster_student_id)
        roster = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertFalse(roster['required']); self.assertTrue(roster['available'])
        self.assertEqual(roster['students'][0]['name'], 'Коваленко Ірина')

    async def test_pick_imported_pupil_or_enter_other_name(self):
        self.key(); report = json.loads((await self.send(self.roster_payload()))[1])
        pupil_id = report['mappings'][0]['local_id']
        selected = await fixtures.http('POST', '/student/login', {'student_name': 'Ков. І.', 'access_code': 'CHECK1', 'roster_student_id': pupil_id}, form=True)
        self.assertEqual(selected[0], 303)
        attempt = self.db.query(models.StudentAttempt).filter_by(roster_student_id=pupil_id).one()
        self.assertEqual(attempt.student_name, 'Коваленко Ірина')
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


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        fixtures.engine.dispose()
        fixtures.WORK.cleanup()
