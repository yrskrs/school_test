"""Class-scoped review through the real HTTP forms, on disposable SQLite."""
import html
import re
import unittest
import uuid

import test_flows as fixtures

from app import crud, models
from app.services.roster_backend import operate
from school_sync.core import Engine
from school_sync.tests.test_protocol import Native, Store


class RosterReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    def seed_exchange(self, names=None):
        names = names or [('Коваленко', 'Ірина'), ('Шевченко', 'Олег')]
        self.group = models.RosterClass(name='7-А', grade_level=7, letter='А')
        self.foreign = models.RosterClass(name='9-Б', grade_level=9, letter='Б')
        self.db.add_all([self.group, self.foreign]); self.db.flush()
        self.pupils = [models.RosterStudent(class_id=self.group.id, last_name=last, first_name=first)
                       for last, first in names]
        self.foreign_pupil = models.RosterStudent(class_id=self.foreign.id, last_name='Чужий', first_name='Учень')
        self.db.add_all([*self.pupils, self.foreign_pupil]); self.db.commit()
        self.source = Engine(Store(), Native())
        source_class = self.source.native.create('class', {'name': '7-А', 'grade_level': 7, 'letter': 'А', 'active': True})
        for last, first in names:
            self.source.native.create('student', {'name': last+' '+first, 'last_name': last,
                'first_name': first, 'middle_name': '', 'native_class_id': source_class, 'active': True})
        self.source.share('class', source_class)
        self.snapshot = self.source.snapshot()
        operate(lambda engine, store: engine.receive(self.snapshot), self.db)
        self.class_ref = self.snapshot['records'][0]['ref']
        self.student_refs = [row['ref'] for row in self.snapshot['records'] if row['kind'] == 'student']

    async def page(self):
        status, body, _ = await fixtures.http('GET', '/teacher/roster-sync', cookie=self.teacher_cookie)
        self.assertEqual(status, 200)
        return body

    def form(self, body, *, ref=None, action='resolve'):
        forms = re.findall(r'<form\b[^>]*>(.*?)</form>', body, re.DOTALL)
        form = next(form for form in forms if f'name="action" value="{action}"' in form
                    and (ref is None or f'name="ref" value="{ref}"' in form))
        return {name: html.unescape(value) for name, value in re.findall(
            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form)}

    async def post(self, fields):
        status, body, _ = await fixtures.http('POST', '/teacher/roster-sync', fields,
            cookie=self.teacher_cookie, form=True)
        self.assertEqual(status, 200)
        return body

    async def bind_class(self):
        fields = self.form(await self.page(), ref=self.class_ref)
        return await self.post({**fields, 'choice': 'local', 'native_id': self.group.id})

    async def test_class_first_scoped_suggestions_batch_and_automatic_updates_preserve_history(self):
        self.seed_exchange()
        before = await self.page()
        self.assertIn('Спочатку підтвердьте відповідний клас', before)
        after = await self.bind_class()
        select = re.search(r'<select name="native_id" id="native-'+self.student_refs[0]+r'">(.*?)</select>', after, re.DOTALL).group(1)
        self.assertIn('Коваленко Ірина', select)
        self.assertNotIn('Чужий Учень', select)
        fields = self.form(after, action='resolve_matches')
        self.attempt.roster_student_id = self.pupils[0].id
        self.db.commit()
        result = await self.post(fields)
        self.assertIn('Знайдені відповідності підтверджено', result)
        self.assertNotIn('name="action" value="resolve_matches"', result)
        self.db.expire_all()
        self.assertEqual(self.db.query(models.RosterStudent).count(), 3)
        self.assertEqual(self.attempt.roster_student_id, self.pupils[0].id)
        operate(lambda engine, store: engine.receive(self.snapshot), self.db)
        self.source.receive(operate(lambda engine, store: engine.snapshot(), self.db))
        pupil = self.source.native.rows['student'][1]
        pupil.update(first_name='Іринка', name='Коваленко Іринка')
        operate(lambda engine, store: engine.receive(self.source.snapshot()), self.db)
        self.db.expire_all()
        self.assertEqual(self.pupils[0].first_name, 'Іринка')
        self.assertEqual(self.attempt.roster_student_id, self.pupils[0].id)
        self.pupils[0].first_name = 'Локальна'; self.db.commit()
        pupil.update(first_name='З журналу', name='Коваленко З журналу')
        operate(lambda engine, store: engine.receive(self.source.snapshot()), self.db)
        self.assertIn('Перевірте зміни', await self.page())

    async def test_stale_bulk_rolls_back_every_binding(self):
        self.seed_exchange()
        fields = self.form(await self.bind_class(), action='resolve_matches')
        self.pupils[1].first_name = 'Зміна після перегляду'; self.db.commit()
        body = await self.post(fields)
        self.assertIn('Список змінився після підбору', body)
        rows = operate(lambda engine, store: store.all(), self.db)
        self.assertTrue(all(row.get('native_id') is None for row in rows if row['kind'] == 'student'))

    async def test_new_twin_after_preview_invalidates_bulk(self):
        self.seed_exchange()
        fields = self.form(await self.bind_class(), action='resolve_matches')
        self.db.add(models.RosterStudent(class_id=self.group.id, first_name='Ірина', last_name='Коваленко'))
        self.db.commit()
        body = await self.post(fields)
        self.assertIn('Список змінився після підбору', body)
        row = operate(lambda engine, store: store.get(self.student_refs[0]), self.db)
        self.assertIsNone(row['native_id'])

    async def test_changed_candidate_invalidates_single_confirmation(self):
        self.seed_exchange()
        fields = self.form(await self.bind_class(), ref=self.student_refs[0])
        self.pupils[0].first_name = 'Інше ім’я'; self.db.commit()
        body = await self.post({**fields, 'choice': 'local', 'native_id': self.pupils[0].id})
        self.assertIn('Вибраний запис змінився після перегляду', body)
        row = operate(lambda engine, store: store.get(self.student_refs[0]), self.db)
        self.assertIsNone(row['native_id'])

    async def test_archive_csv_uses_session_class_and_preserves_scope(self):
        self.seed_exchange()
        self.session.roster_class_id = self.group.id
        self.attempt.status, self.attempt.score, self.attempt.max_score = models.AttemptStatus.finished, 1, 1
        self.attempt.student_name, self.attempt.is_archived = 'Архівний Учень', True
        active = crud.create_attempt(self.db, self.session.id, 'Активний Учень')
        active.status, active.score, active.max_score = models.AttemptStatus.finished, 1, 1
        self.db.commit()
        from urllib.parse import urlencode
        query = urlencode({'pupil_scope': 'outside', 'tab': 'archive', 'class_name': self.group.name})
        status, body, _ = await fixtures.http('GET', '/teacher/results/export?'+query, cookie=self.teacher_cookie)
        self.assertEqual(status, 200)
        self.assertIn('Архівний Учень', body)
        self.assertIn(self.group.name, body)
        self.assertNotIn('Активний Учень', body)

    async def test_twins_require_individual_review_and_cannot_claim_same_id(self):
        self.seed_exchange([('Коваленко', 'Ірина'), ('Коваленко', 'Ірина')])
        body = await self.bind_class()
        self.assertNotIn('name="action" value="resolve_matches"', body)
        first = self.form(body, ref=self.student_refs[0])
        body = await self.post({**first, 'choice': 'local', 'native_id': self.pupils[0].id})
        second = self.form(body, ref=self.student_refs[1])
        body = await self.post({**second, 'choice': 'local', 'native_id': self.pupils[0].id})
        self.assertIn('Вибраний запис змінився після перегляду', body)
        second = self.form(body, ref=self.student_refs[1])
        body = await self.post({**second, 'choice': 'local', 'native_id': self.pupils[1].id})
        self.assertIn('Рішення синхронізації успішно застосовано', body)

    async def test_foreign_class_target_and_forged_batch_are_rejected(self):
        self.seed_exchange()
        body = await self.bind_class()
        fields = self.form(body, ref=self.student_refs[0])
        body = await self.post({**fields, 'choice': 'local', 'native_id': self.foreign_pupil.id})
        self.assertIn('Вибраний запис змінився після перегляду', body)
        fields = self.form(body, action='resolve_matches')
        body = await self.post({**fields, 'matches': fields['matches']+'tampered'})
        self.assertIn('Недійсна версія підбору', body)

    async def test_similar_name_is_suggested_without_bulk_merge(self):
        self.seed_exchange()
        self.pupils[0].first_name = 'Іринка'; self.db.commit()
        body = await self.bind_class()
        self.assertIn('Схоже ім’я — перевірте уважно', body)
        self.assertIn('Підтвердити точні збіги (1)', body)
        rows = operate(lambda engine, store: store.all(), self.db)
        self.assertTrue(all(row.get('native_id') is None for row in rows if row['kind'] == 'student'))

    async def test_outside_tab_counts_archived_and_keeps_teacher_scope(self):
        for owner, archived in ((self.owner, False), (self.owner, True), (self.other, False)):
            test = self.test if owner == self.owner else crud.create_test(self.db, owner.id, {'title': 'Інший тест'})
            session = self.session if owner == self.owner else crud.create_session(self.db, test.id, 'OTHER1')
            attempt = crud.create_attempt(self.db, session.id, 'Окремий Учень' if owner == self.owner else 'Чужий Учень', outside_student_id=str(uuid.uuid4()))
            attempt.status, attempt.score, attempt.max_score, attempt.is_archived = models.AttemptStatus.finished, 1, 1, archived
        self.db.commit()
        body = await self.page()
        self.assertIn('id="tab-btn-outside"', body)
        outside = body.split('id="tab-pane-outside"')[1].split('{% endblock %}')[0]
        self.assertIn('Активних спроб: <strong>1</strong>', outside)
        self.assertIn('В архіві: <strong>1</strong>', outside)
        self.assertNotIn('Чужий Учень', outside)
        self.assertNotIn('<p><a class="btn btn-primary" href="/teacher/results?pupil_scope=outside">', body)
        status, result, _ = await fixtures.http('GET', '/teacher/results?pupil_scope=outside&tab=archive', cookie=self.teacher_cookie)
        self.assertEqual(status, 200)
        self.assertIn('Результати окремих учнів', result)
        self.assertIn('← До окремих учнів', result)
