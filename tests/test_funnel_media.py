"""Transport regression checks; no application imports or working database."""
import asyncio
import json
from pathlib import Path
import unittest
from urllib.parse import urlencode, parse_qs


def load_adapter(path):
    namespace = {}
    source = Path(path).read_text()
    exec(compile(source, str(path), 'exec'), namespace)
    namespace['PUBLIC_HOST'] = 'localwork.tail9ec47d.ts.net'
    return namespace


class MediaTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = load_adapter(Path(__file__).resolve().parents[1] / 'app' / 'funnel.py')

    def exchange(self, data, host='localwork.tail9ec47d.ts.net', form=None, attachment=False):
        captured = {}
        output = []
        async def application(scope, receive, send):
            captured['scope'] = scope
            if form is not None:
                captured['request'] = await receive()
            payload = json.dumps(data, ensure_ascii=False).encode()
            response_headers = [(b'content-type', b'application/json'), (b'content-length', str(len(payload)).encode())]
            if attachment: response_headers.append((b'content-disposition', b'attachment; filename=test.json'))
            await send({'type': 'http.response.start', 'status': 200, 'headers': response_headers})
            midpoint = len(payload) // 2
            await send({'type': 'http.response.body', 'body': payload[:midpoint], 'more_body': True})
            await send({'type': 'http.response.body', 'body': payload[midpoint:], 'more_body': False})
        async def receive():
            return {'type': 'http.request', 'body': form or b'', 'more_body': False}
        async def send(message):
            output.append(message)
        scope = {'type': 'http', 'path': '/test/teacher/upload-image', 'method': 'POST', 'headers': [(b'host', host.encode())]}
        if form is not None:
            scope['headers'].append((b'content-type', b'application/x-www-form-urlencoded'))
        asyncio.run(self.adapter['PrefixApp'](application)(scope, receive, send))
        body = b''.join(m.get('body', b'') for m in output)
        captured['response'] = json.loads(body)
        self.assertEqual(int(dict(output[0]['headers'])[b'content-length']), len(body))
        return captured

    def test_uploaded_image_url_uses_public_mount(self):
        self.assertEqual(self.exchange({'url': '/static/tests/Урок/image.png'})['response']['url'], '/test/static/tests/Урок/image.png')

    def test_nested_question_and_answer_media(self):
        data = {'questions': [{'image_url': '/static/tests/q.png', 'options': [{'image_url': '/static/uploads/o.png'}]}]}
        actual = self.exchange(data)['response']['questions'][0]
        self.assertEqual(actual['image_url'], '/test/static/tests/q.png')
        self.assertEqual(actual['options'][0]['image_url'], '/test/static/uploads/o.png')

    def test_external_prefixed_text_and_redirect(self):
        data = {'image_url': 'https://example.org/x.png', 'url': '/test/static/x.png', 'question_text': '/static/is-question-text', 'redirect': '/teacher/tests'}
        actual = self.exchange(data)['response']
        self.assertEqual(actual, {**data, 'redirect': '/test/teacher/tests'})

    def test_lan_response_and_save_unchanged(self):
        data = {'url': '/static/uploads/x.png'}
        form = urlencode({'questions_json': json.dumps([{'image_url': '/static/x.png'}])}).encode()
        actual = self.exchange(data, host='192.168.1.13:8000', form=form)
        self.assertEqual(actual['response'], data)
        self.assertEqual(actual['request']['body'], form)

    def test_export_attachment_keeps_portable_paths(self):
        data = {'questions': [{'image_url': '/static/tests/export.png'}]}
        self.assertEqual(self.exchange(data, attachment=True)['response'], data)

    def test_monitor_script_keeps_api_and_websocket_routes_on_public_mount(self):
        source = (Path(__file__).resolve().parents[1] / 'app/static/js/monitor.js').read_bytes()

        async def exchange(host):
            messages = []

            async def application(scope, receive, send):
                await send({'type': 'http.response.start', 'status': 200,
                            'headers': [(b'content-type', b'application/javascript')]})
                await send({'type': 'http.response.body', 'body': source})

            async def receive():
                return {'type': 'http.request', 'body': b''}

            async def send(message):
                messages.append(message)

            await self.adapter['PrefixApp'](application)(
                {'type': 'http', 'path': '/test/static/js/monitor.js',
                 'headers': [(b'host', host.encode())]}, receive, send)
            return b''.join(m.get('body', b'') for m in messages).decode()

        public = asyncio.run(exchange('localwork.tail9ec47d.ts.net'))
        self.assertIn('fetch(`/test/api/session-status/${SESSION_ID}`)', public)
        self.assertIn('${location.host}/test/ws/monitor/${SESSION_ID}', public)
        self.assertIn('monitor-attempt-labels', public)
        self.assertEqual(asyncio.run(exchange('192.168.1.13:8000')), source.decode())

    def test_no_configured_public_host_keeps_original_response(self):
        host = self.adapter['PUBLIC_HOST']
        self.adapter['PUBLIC_HOST'] = ''
        try:
            data = {'url': '/static/uploads/x.png'}
            self.assertEqual(self.exchange(data)['response'], data)
        finally:
            self.adapter['PUBLIC_HOST'] = host

    def test_http_guard_limits_requests_and_does_not_queue_websockets(self):
        async def check():
            entered = 0
            peak = 0
            release = asyncio.Event()
            websocket_passed = asyncio.Event()
            async def application(scope, receive, send):
                nonlocal entered, peak
                if scope['type'] == 'websocket':
                    websocket_passed.set()
                    return
                entered += 1
                peak = max(peak, entered)
                await release.wait()
                entered -= 1
            guard = self.adapter['HTTPConcurrencyGuard'](application, limit=2)
            async def noop(*args): pass
            requests = [asyncio.create_task(guard({'type':'http'}, noop, noop)) for _ in range(6)]
            await asyncio.sleep(0)
            self.assertEqual(entered, 2)
            await guard({'type':'websocket'}, noop, noop)
            self.assertTrue(websocket_passed.is_set())
            release.set()
            await asyncio.gather(*requests)
            self.assertEqual(peak, 2)
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main(verbosity=2)
