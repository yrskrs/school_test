"""File-access regressions with disposable files and the synthetic ASGI fixture."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_flows as fixtures


class AuditFileAccess(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def test_legacy_answer_keys_are_blocked_after_path_normalization(self):
        mount = next(route for route in fixtures.app.routes if route.path == '/static')
        with tempfile.TemporaryDirectory(prefix='synthetic-static-') as directory:
            root = Path(directory)
            folder = root / 'tests' / 'synthetic'
            folder.mkdir(parents=True)
            (folder / 'test.json').write_text('{"private_answer": "synthetic"}')
            (folder / 'image.png').write_bytes(b'synthetic-image')
            with patch.object(mount.app, 'all_directories', [directory]):
                for suffix in ('test.json', 'test.json/', 'test.json/.', 'test.json/./',
                               'missing/../test.json/.'):
                    with self.subTest(suffix=suffix):
                        status, body, _ = await fixtures.http('GET', '/static/tests/synthetic/' + suffix)
                        self.assertEqual(status, 404)
                        self.assertNotIn('private_answer', body)
                status, _, _ = await fixtures.http('GET', '/static/tests/synthetic/image.png')
                self.assertEqual(status, 200)

    async def test_security_headers_cover_login_static_and_errors(self):
        for path in ('/teacher/login', '/static/css/styles.css', '/missing-audit-page'):
            with self.subTest(path=path):
                _, _, headers = await fixtures.http('GET', path)
                self.assertEqual(headers[b'x-content-type-options'], b'nosniff')
                self.assertEqual(headers[b'x-frame-options'], b'DENY')


if __name__ == '__main__':
    unittest.main()
