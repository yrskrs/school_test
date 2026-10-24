"""Class formatting and metadata regressions on the disposable ASGI fixture."""
import json
import re
import sys
import unittest
from pathlib import Path

for path in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import test_flows as fixtures
from app import models
from app.services.class_labels import class_fields
from app.services.roster_service import resolve_roster_class
from app.services.session_service import start_new_session


class ClassLabels(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    def group(self, name='9-Г', active=True):
        group = models.RosterClass(name=name, grade_level=9, letter='Г', active=active)
        self.db.add(group); self.db.flush()
        self.db.add(models.RosterStudent(class_id=group.id, first_name='Учень', last_name='Синтетичний'))
        self.db.commit()
        return group

    async def test_selected_profile_label_finds_the_same_roster_with_a_dash(self):
        group = self.group()
        self.test.class_name = '9Г'; self.db.commit()
        status, body, _ = await fixtures.http('GET', '/api/session-roster?code=CHECK1')
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload['class_id'], group.id)
        self.assertEqual(len(payload['students']), 1)
        session = start_new_session(self.db, self.test.id)
        self.assertEqual(session.roster_class_id, group.id)
        self.test.class_name = '7-А'; self.db.commit()
        resolved, error = resolve_roster_class(self.db, self.test, session.roster_class_id)
        self.assertEqual(resolved.id, group.id)
        self.assertEqual(error, '')

    async def test_equivalent_names_with_different_ids_require_explicit_binding(self):
        first, second = self.group(), self.group('9Г')
        self.test.class_name = '9Г'; self.db.commit()
        resolved, error = resolve_roster_class(self.db, self.test)
        self.assertIsNone(resolved)
        self.assertIn('кільком', error)
        self.assertEqual(resolve_roster_class(self.db, self.test, first.id)[0].id, first.id)
        self.assertEqual(resolve_roster_class(self.db, self.test, second.id)[0].id, second.id)

    async def test_inactive_normalized_class_is_not_an_available_student_list(self):
        self.group(active=False)
        self.test.class_name = '9Г'; self.db.commit()
        payload = json.loads((await fixtures.http('GET', '/api/session-roster?code=CHECK1'))[1])
        self.assertIn('неактивний', payload['error'])
        self.assertEqual(payload['students'], [])

    async def test_manual_class_form_infers_parallel_and_letter_from_its_name(self):
        page = await fixtures.http('GET', '/teacher/roster-sync', cookie=self.teacher_cookie)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page[1]).group(1)
        result = await fixtures.http('POST', '/teacher/roster-sync', {
            'csrf': csrf, 'action': 'class', 'name': '10-В', 'grade': '1', 'letter': '', 'active': '1'
        }, self.teacher_cookie, form=True)
        self.assertEqual(result[0], 200)
        group = self.db.query(models.RosterClass).filter_by(name='10-В').one()
        self.assertEqual((group.grade_level, group.letter), (10, 'В'))

    async def test_class_labels_are_narrow_and_do_not_guess_custom_groups(self):
        for label in ('9Г', '9-Г', '9 г', '9 «Г»', '9—Г'):
            self.assertEqual(class_fields(label), {'grade_level': 9, 'letter': 'Г'})
        self.assertEqual(class_fields('10B'), {'grade_level': 10, 'letter': 'В'})
        for label in ('Група 9Г', '9-Г (Позакласні)', '13-А', '0-А', '9/10'):
            self.assertIsNone(class_fields(label))


if __name__ == '__main__':
    unittest.main()
