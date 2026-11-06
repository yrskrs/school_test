"""Performance regressions exercise rendered transport and ORM work, not stored scores."""
import asyncio
import gzip
import json
import os
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
_BACKUP_TEMP = tempfile.TemporaryDirectory(prefix='monitor-test-backups-')
os.environ.setdefault('CLOUD_BACKUP_ROOT', _BACKUP_TEMP.name)
import test_flows as fixtures
from sqlalchemy import event
from starlette.middleware.gzip import GZipMiddleware
from app import crud, models
from app.services.attempt_display import monitor_attempt_data
from test_funnel_media import load_adapter


class MonitorQueryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def test_dashboard_chart_initialization_follows_existing_canvases(self):
        response = await fixtures.http('GET','/teacher/dashboard',cookie=self.teacher_cookie)
        self.assertEqual(response[0],200)
        html = response[1]
        self.assertIn('id="activityChart"',html)
        self.assertIn('dashboard-charts.js',html)
        self.assertLess(html.index('id="activityChart"'),html.index('dashboard-charts.js'))

    async def test_progress_query_count_stays_bounded_for_a_class(self):
        session_id = self.session.id
        question_id = self.test.questions[0].id
        for number in range(25):
            attempt = crud.create_attempt(self.db, session_id, f'Учень {number}')
            self.db.add(models.StudentAnswer(attempt_id=attempt.id, question_id=question_id, is_correct=True))
        self.db.commit()
        self.db.expunge_all()
        statements = []
        def count(*args): statements.append(args[2])
        event.listen(self.db.bind, 'before_cursor_execute', count)
        try:
            attempts = crud.get_attempts_by_session(self.db, session_id, with_progress=True)
            payload = monitor_attempt_data(attempts, {})
            self.assertGreaterEqual(len(payload), 25)
            self.assertLessEqual(len(statements), 2)
            self.assertTrue(any(a['assigned_questions'] for a in payload))
        finally:
            event.remove(self.db.bind, 'before_cursor_execute', count)


class PrefixCompressionTests(unittest.TestCase):
    def test_compression_preserves_rewritten_media_and_portable_download(self):
        adapter = load_adapter(Path(__file__).resolve().parents[1] / 'app/funnel.py')
        async def exercise(attachment):
            messages = []
            payload = json.dumps({'image_url':'/static/uploads/example.png','text':'перевірка '*1000}, ensure_ascii=False).encode()
            async def application(scope, receive, send):
                self.assertNotIn(b'accept-encoding', dict(scope['headers']))
                headers = [(b'content-type',b'application/json')]
                if attachment: headers.append((b'content-disposition',b'attachment; filename=test.json'))
                await send({'type':'http.response.start','status':200,'headers':headers})
                await send({'type':'http.response.body','body':payload})
            async def receive(): return {'type':'http.request','body':b''}
            async def send(message): messages.append(message)
            app = GZipMiddleware(adapter['PrefixApp'](application), minimum_size=1000, compresslevel=5)
            scope = {'type':'http','path':'/test/example','method':'GET','headers':[(b'host',b'localwork.tail9ec47d.ts.net'),(b'accept-encoding',b'gzip')]}
            await app(scope,receive,send)
            headers = dict(messages[0]['headers'])
            compressed = b''.join(m.get('body',b'') for m in messages[1:])
            self.assertEqual(headers[b'content-encoding'],b'gzip')
            self.assertIn(b'Accept-Encoding',headers[b'vary'])
            self.assertLess(len(compressed),len(payload)//3)
            data = json.loads(gzip.decompress(compressed))
            self.assertEqual(data['image_url'], '/static/uploads/example.png' if attachment else '/test/static/uploads/example.png')
        asyncio.run(exercise(False))
        asyncio.run(exercise(True))
