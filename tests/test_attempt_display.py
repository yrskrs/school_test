"""History identities, completed-only results and active-first sorting."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest

for path in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import test_flows as fixtures
from app import crud, models
from app.services.attempt_display import student_identity
from app.services.teacher_list_service import build_list


class AttemptDisplay(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    def finished(self, name, session=None, **identity):
        a = crud.create_attempt(self.db, (session or self.session).id, name, **identity)
        a.status, a.score, a.max_score, a.finished_at = models.AttemptStatus.finished, 1, 2, datetime.now()
        self.db.commit()
        return a

    async def test_numbers_use_ids_and_never_merge_roster_namesakes(self):
        group = models.RosterClass(name='7-А')
        self.db.add(group); self.db.flush()
        pupils = [models.RosterStudent(class_id=group.id, first_name='Іван', last_name='Іван') for _ in range(2)]
        self.db.add_all(pupils); self.db.commit()
        first = self.finished('Іван Іван', roster_student_id=pupils[0].id)
        second = self.finished('Виправлене ім’я', roster_student_id=pupils[0].id)
        namesake = self.finished('Іван Іван', roster_student_id=pupils[1].id)
        response = await fixtures.http('GET', f'/api/session-status/{self.session.id}', cookie=self.teacher_cookie)
        self.assertEqual(response[0], 200)
        rows = {r['id']: r for r in json.loads(response[1])['attempts']}
        self.assertEqual([rows[a.id]['session_attempt_number'] for a in (first,second,namesake)], [1,2,1])
        self.assertEqual(rows[first.id]['student_key'], rows[second.id]['student_key'])
        self.assertNotEqual(rows[first.id]['student_key'], rows[namesake.id]['student_key'])
        self.assertEqual(rows[namesake.id]['session_attempt_count'], 1)
        details = await fixtures.http('GET', f'/teacher/results/{first.id}', cookie=self.teacher_cookie)
        self.assertIn(f'href="/teacher/results/{second.id}"', details[1])
        self.assertNotIn(f'href="/teacher/results/{namesake.id}"', details[1])

    async def test_legacy_completed_history_groups_only_within_its_session(self):
        first = self.finished('Іван  Іван')
        second = self.finished('  іван іван  ')
        other_session = crud.create_session(self.db, self.test.id, 'OTHER2')
        third = self.finished('Іван Іван', session=other_session)
        self.assertEqual(student_identity(first), student_identity(second))
        self.assertNotEqual(student_identity(first), student_identity(third))
        page = await fixtures.http('GET', '/teacher/results', cookie=self.teacher_cookie)
        self.assertEqual(re.search(r'Згруповано \((\d+)\)', page[1]).group(1), '2')
        self.assertIn('Спроби (2)', page[1])
        self.assertNotIn('Student Example', page[1])  # The unfinished fixture is not a result.
        details = await fixtures.http('GET', f'/teacher/results/{first.id}', cookie=self.teacher_cookie)
        self.assertIn(f'href="/teacher/results/{second.id}"', details[1])
        self.assertNotIn(f'href="/teacher/results/{third.id}"', details[1])

    async def test_outside_ids_remain_separate_and_archive_is_preserved(self):
        first = self.finished('Те саме ім’я', outside_student_id='outside-a')
        second = self.finished('Те саме ім’я', outside_student_id='outside-b')
        self.assertNotEqual(student_identity(first), student_identity(second))
        second.is_archived = True; self.db.commit()
        for tab in ('active', 'archive'):
            page = await fixtures.http('GET', f'/teacher/results?tab={tab}', cookie=self.teacher_cookie)
            self.assertEqual(re.search(r'Згруповано \((\d+)\)', page[1]).group(1), '1')
        first.status = models.AttemptStatus.stopped; self.db.commit()
        for mode in ('grouped', 'all'):
            page = await fixtures.http('GET', f'/teacher/results?view_mode={mode}', cookie=self.teacher_cookie)
            self.assertIn('Зупинено', page[1])

    async def test_session_sort_always_keeps_active_before_closed(self):
        self.session.started_at = datetime(2020,1,1)
        active = crud.create_session(self.db, self.test.id, 'ZZSORT')
        active.started_at = datetime(2020,1,2)
        closed = crud.create_session(self.db, self.test.id, 'AASORT')
        closed.started_at, closed.is_active = datetime(2030,1,1), False
        self.db.commit()
        for sort in ('newest', 'oldest', 'title', 'code', 'invalid'):
            context = build_list(self.db,self.owner.id,'sessions',page=1,per_page=20,tab='active',search='',class_filter='',state='all',sort=sort)
            rows = [r['session'] for r in context['sessions_data']]
            self.assertEqual(rows[-1].id, closed.id)
            self.assertTrue(all(s.is_active for s in rows[:-1]))
            if sort=='oldest': self.assertEqual(rows[0].id,self.session.id)
        page = await fixtures.http('GET','/teacher/sessions?sort=oldest',cookie=self.teacher_cookie)
        self.assertIn('value="oldest" selected',page[1])

    async def test_active_priority_applies_before_pagination(self):
        for i in range(11): crud.create_session(self.db,self.test.id,f'PAG{i:03}')
        closed = crud.create_session(self.db,self.test.id,'CLOSED')
        closed.is_active=False; self.db.commit()
        context = build_list(self.db,self.owner.id,'sessions',page=1,per_page=10,tab='active',search='',class_filter='',state='all',sort='code')
        self.assertEqual(len(context['sessions_data']),10)
        self.assertTrue(all(r['session'].is_active for r in context['sessions_data']))


if __name__ == '__main__': unittest.main()
