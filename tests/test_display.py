"""Question metadata and monitoring regression checks on disposable data."""
import json
import unittest

from sqlalchemy import event
from sqlalchemy.orm import raiseload

import test_flows as fixtures
from app import crud, models
from app.database import engine
from app.security import set_student_cookie
from app.services import test_service


class DisplayChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    def bank(self):
        return crud.create_test(self.db, self.owner.id, {
            'title': 'Банк на 200 питань', 'random_questions_limit': 25,
            'excluded_topics': json.dumps(['Прихована']),
            'questions': [{'question_text': f'Питання {i}', 'topic': 'Прихована' if i < 20 else '',
                           'order_index': i, 'question_type': 'single_choice',
                           'options': [{'option_text': 'Так', 'is_correct': True}, {'option_text': 'Ні'}]}
                          for i in range(200)],
        })

    async def test_bank_preview_matches_assigned_questions_and_login_errors(self):
        test = self.bank()
        session = crud.create_session(self.db, test.id, 'BANK01')
        data = json.loads((await fixtures.http('GET', '/api/session-roster?code=BANK01'))[1])
        self.assertEqual((data['question_count'], data['assigned_question_count']), (200, 25))
        for method, path, fields in (
            ('GET', '/student/login?code=BANK01', None),
            ('POST', '/student/login', {'access_code': 'BANK01', 'student_name': ''}),
        ):
            response = await fixtures.http(method, path, fields, form=method == 'POST')
            self.assertEqual(response[0], 200 if method == 'GET' else 400)
            self.assertIn('Усього в тесті: 200', response[1])
            self.assertIn('Буде задано: 25', response[1])
        session.is_pinned = True
        self.db.commit()
        self.assertIn('Буде задано: 25', (await fixtures.http('GET', '/student/login'))[1])
        attempt = crud.create_attempt(self.db, session.id, 'Учень')
        cookie = fixtures.cookie_for(set_student_cookie, attempt.id)
        self.assertIn('25', (await fixtures.http('GET', f'/student/instruction/{attempt.id}', cookie=cookie))[1])
        assigned = test_service.get_attempt_questions(self.db, attempt)
        self.assertEqual(len(assigned), data['assigned_question_count'])
        self.assertTrue(all(q.topic != 'Прихована' for q in assigned))
        test.random_questions_limit = 5
        self.db.commit()
        # Existing attempts keep their original assignment after settings change.
        self.assertEqual(len(test_service.get_attempt_questions(self.db, attempt)), 25)
        instruction = (await fixtures.http('GET', f'/student/instruction/{attempt.id}', cookie=cookie))[1]
        self.assertIn('class="istat-value">25</span>', instruction)

    async def test_limits_exclusions_and_empty_bank_without_loading_bodies(self):
        test_id = self.test.id
        self.db.expire_all()
        test = self.db.query(models.Test).options(raiseload(models.Test.questions)).filter_by(id=test_id).one()
        for excluded, limit, expected in (
            ('[]', None, 8), ('[]', 3, 3), ('[]', 99, 8),
            ('[""]', 3, 0), ('["missing"]', 3, 3), ('bad-json', 2, 2),
        ):
            with self.subTest(excluded=excluded, limit=limit):
                test.excluded_topics, test.random_questions_limit = excluded, limit
                self.db.commit()
                self.assertEqual(test_service.question_counts(self.db, test),
                                 {'question_count': 8, 'assigned_question_count': expected})
        empty = crud.create_test(self.db, self.owner.id, {'title': 'Порожній'})
        self.assertEqual(test_service.question_counts(self.db, empty),
                         {'question_count': 0, 'assigned_question_count': 0})

    async def test_monitor_queries_stay_bounded_and_payload_is_unchanged(self):
        async def measure():
            statements = []
            def record(conn, cursor, statement, params, context, many):
                if statement.lstrip().upper().startswith('SELECT'):
                    statements.append(statement)
            event.listen(engine, 'before_cursor_execute', record)
            try:
                status, body, _ = await fixtures.http('GET', f'/api/session-status/{self.session.id}', cookie=self.teacher_cookie)
            finally:
                event.remove(engine, 'before_cursor_execute', record)
            self.assertEqual(status, 200)
            return statements, json.loads(body)

        small, _ = await measure()
        for i in range(12):
            attempt = crud.create_attempt(self.db, self.session.id, f'Учень {i}')
            test_service.get_attempt_questions(self.db, attempt)
        large, payload = await measure()
        print(f'Monitor SQL SELECTs: 1 pupil={len(small)}, 13 pupils={len(large)}')
        self.assertEqual(len(payload['attempts']), 13)
        self.assertTrue(all(len(row['assigned_questions']) == 8 for row in payload['attempts']))
        self.assertLessEqual(len(large), len(small) + 1)
        self.assertLessEqual(len(large), 10)
        self.assertNotIn('questions.question_text', '\n'.join(large))
        self.assertNotIn('question_options', '\n'.join(large))
        forbidden = await fixtures.http('GET', f'/api/session-status/{self.session.id}', cookie=self.other_cookie)
        self.assertEqual(forbidden[0], 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
