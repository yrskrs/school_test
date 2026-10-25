"""Kyiv rendering, DST boundaries, persisted UTC and authoritative API times."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

for path in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))

import test_flows as fixtures
from app import models
from app.config import settings
from app.time_utils import format_kyiv, stored_from_kyiv, stored_now, timestamp_iso


class TimeConversions(unittest.TestCase):
    def test_dst_uses_the_submission_date(self):
        self.assertEqual(format_kyiv(datetime(2026, 10, 23, 11, 27, 42)), '23.10.2026 14:27')
        self.assertEqual(format_kyiv(datetime(2026, 10, 25, 11, 27)), '25.10.2026 13:27')
        self.assertEqual(format_kyiv(datetime(2026, 10, 24, 22, 30)), '25.10.2026 01:30')
        self.assertEqual(format_kyiv(datetime(2026, 10, 25, 0, 30)), '25.10.2026 03:30')
        self.assertEqual(format_kyiv(datetime(2026, 10, 25, 1, 30)), '25.10.2026 03:30')

    def test_aware_values_and_legacy_zone(self):
        self.assertEqual(timestamp_iso(datetime(2026, 10, 23, 11, 27)), '2026-10-23T11:27:00+00:00')
        with patch.object(settings, 'LEGACY_RESULT_TIMEZONE', 'Europe/Kyiv'):
            self.assertEqual(timestamp_iso(datetime(2026, 10, 23, 14, 27)), '2026-10-23T11:27:00+00:00')
        self.assertEqual(format_kyiv(datetime(2026, 10, 23, 11, 27, tzinfo=timezone.utc)), '23.10.2026 14:27')
        self.assertIsNone(timestamp_iso(None))

    def test_storage_does_not_depend_on_container_timezone(self):
        previous = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Pacific/Honolulu'
            time.tzset()
            self.assertLess(abs((stored_now()-datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()), 1)
        finally:
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()

    def test_kyiv_calendar_boundaries(self):
        self.assertEqual(stored_from_kyiv(datetime(2026, 10, 23)), datetime(2026, 10, 22, 21))
        self.assertEqual(stored_from_kyiv(datetime(2026, 10, 26)), datetime(2026, 10, 25, 22))


class SubmissionTimePages(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def test_results_export_and_monitor_api_agree(self):
        attempt = self.db.get(models.StudentAttempt, self.attempt.id)
        attempt.started_at = datetime(2026, 10, 23, 11, 17)
        attempt.finished_at = datetime(2026, 10, 23, 11, 27)
        attempt.status = models.AttemptStatus.finished
        attempt.score, attempt.max_score = 1, 2
        self.db.commit()
        status, body, _ = await fixtures.http('GET', f'/api/session-status/{self.session.id}', cookie=self.teacher_cookie)
        self.assertEqual(status, 200)
        row = next(r for r in json.loads(body)['attempts'] if r['id']==attempt.id)
        self.assertEqual(row['finished_at'], '2026-10-23T11:27:00+00:00')
        for url in ['/teacher/results', f'/teacher/sessions/{self.session.id}/monitor']:
            result = await fixtures.http('GET', url, cookie=self.teacher_cookie)
            self.assertEqual(result[0], 200)
            self.assertIn('14:27', result[1])
