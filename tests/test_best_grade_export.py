"""Journal export must use the best final retake on disposable test data."""
import datetime as dt
import json
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_flows as fixtures
from app import crud, models


class BestGradeExportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown
    bind_roster = fixtures.Flows.bind_roster

    def seed(self):
        self.group, self.subject, self.pupils = self.bind_roster()
        self.path = (f'/api/v2/journal/grades/?class_id={self.group.id}&subject_id={self.subject.id}'
                     '&date_from=2026-09-01&date_to=2026-09-30')
        self.attempt.updated_at = dt.datetime(2026, 9, 5, 9, tzinfo=dt.timezone.utc)
        self.db.commit()

    def retake(self, score, maximum=12, status=models.AttemptStatus.finished, session=None, pupil=None):
        row = crud.create_attempt(self.db, (session or self.session).id, 'Synthetic Pupil',
                                  roster_student_id=(pupil or self.pupils[0]).id)
        row.score, row.max_score, row.status = score, maximum, status
        row.finished_at = None if status == models.AttemptStatus.in_progress else dt.datetime(2026, 9, 5, 13)
        row.updated_at = dt.datetime(2026, 9, 5, 12, tzinfo=dt.timezone.utc)
        self.db.commit()
        return row

    async def export(self, cursor=None):
        path = self.path + (f'&cursor={cursor}' if cursor is not None else '')
        status, body, _ = await fixtures.http('GET', path, authorization='Bearer synthetic-v2')
        self.assertEqual(status, 200, body)
        return json.loads(body)

    async def test_lower_last_retake_preserves_best_and_latest_record_revision(self):
        self.seed()
        self.attempt.score = 10
        self.attempt.updated_at = dt.datetime(2026, 9, 5, 9, tzinfo=dt.timezone.utc)
        self.db.commit()
        first = (await self.export())['grades'][0]
        last = self.retake(6)
        row = (await self.export())['grades'][0]
        self.assertEqual((row['value'], row['attempt_id'], row['id']), ('10', self.attempt.id, first['id']))
        self.assertEqual(dt.datetime.fromisoformat(row['updated_at']), dt.datetime(2026, 9, 5, 12, tzinfo=dt.timezone.utc))
        self.assertNotEqual(row['attempt_id'], last.id)
        self.assertEqual((await self.export(cursor=self.attempt.id))['grades'], [])

    async def test_unfinished_or_invalid_last_retake_does_not_hide_final_result(self):
        self.seed()
        for score, maximum, status in [(None, None, models.AttemptStatus.in_progress),
                                        (None, 12, models.AttemptStatus.finished),
                                        (0, 0, models.AttemptStatus.finished)]:
            self.retake(score, maximum, status)
            row = (await self.export())['grades'][0]
            self.assertEqual((row['value'], row['attempt_id']), ('8', self.attempt.id))

    async def test_percentage_wins_over_raw_points_and_latest_breaks_ties(self):
        self.seed()
        best = self.retake(9, 10)
        self.retake(10, 12)
        row = (await self.export())['grades'][0]
        self.assertEqual((row['value'], row['attempt_id']), ('11', best.id))
        tied = self.retake(18, 20, models.AttemptStatus.timeout)
        self.assertEqual((await self.export())['grades'][0]['attempt_id'], tied.id)
        stopped = self.retake(12, 12, models.AttemptStatus.stopped)
        self.assertEqual((await self.export())['grades'][0]['attempt_id'], stopped.id)

    async def test_students_and_independent_sessions_do_not_share_best_mark(self):
        self.seed()
        self.retake(4, pupil=self.pupils[1])
        session = crud.create_session(self.db, self.test.id, 'OTHERSESSION')
        session.roster_class_id, session.roster_subject_id = self.group.id, self.subject.id
        session.lesson_date = '2026-09-06'
        self.db.commit()
        other = self.retake(3, session=session)
        rows = (await self.export())['grades']
        self.assertEqual(len(rows), 3)
        self.assertEqual({r['attempt_id']: r['value'] for r in rows}[other.id], '3')
        self.assertEqual({r['attempt_id']: r['value'] for r in rows}[self.attempt.id], '8')

    async def test_outside_student_retakes_also_keep_best_result(self):
        self.seed()
        import uuid
        identity = str(uuid.uuid4())
        self.attempt.roster_student_id = None
        self.attempt.outside_student_id = identity
        self.db.commit()
        last = crud.create_attempt(self.db, self.session.id, 'Outside Pupil', outside_student_id=identity)
        last.score, last.max_score, last.status = 4, 12, models.AttemptStatus.finished
        last.finished_at = dt.datetime(2026, 9, 5, 13)
        self.db.commit()
        rows = (await self.export())['grades']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['value'], rows[0]['attempt_id'], rows[0]['student_key']),
                         ('8', self.attempt.id, 'outside:' + identity))

    async def test_pagination_returns_all_best_results_once(self):
        self.seed()
        # Each best attempt predates its lower last retake. Cross the API's
        # 500-record boundary to check that cursors still address chosen results.
        for index in range(500):
            pupil = models.RosterStudent(class_id=self.group.id, first_name='Synthetic', last_name=str(index))
            self.db.add(pupil)
            self.db.flush()
            for score in (10, 4):
                self.db.add(models.StudentAttempt(session_id=self.session.id,
                    student_name='Synthetic', roster_student_id=pupil.id,
                    score=score, max_score=12, status=models.AttemptStatus.finished,
                    finished_at=dt.datetime(2026, 9, 5, 13)))
        self.db.commit()
        first = await self.export()
        self.assertEqual(len(first['grades']), 500)
        self.assertIsNotNone(first['next_cursor'])
        second = await self.export(first['next_cursor'])
        self.assertEqual(len(second['grades']), 1)
        self.assertIsNone(second['next_cursor'])
        rows = first['grades'] + second['grades']
        self.assertEqual(len({r['id'] for r in rows}), 501)
        self.assertEqual(sum(r['value'] == '10' for r in rows), 500)


if __name__ == '__main__':
    unittest.main()
