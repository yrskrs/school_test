"""File-access regressions with disposable files and the synthetic ASGI fixture."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

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

    async def test_funnel_prefix_keeps_legacy_answer_keys_blocked(self):
        from app.funnel import PrefixApp
        mount = next(route for route in fixtures.app.routes if route.path == '/static')
        with tempfile.TemporaryDirectory(prefix='synthetic-funnel-static-') as directory:
            folder = Path(directory) / 'tests' / 'synthetic'
            folder.mkdir(parents=True)
            (folder / 'test.json').write_text('{"private_answer": "synthetic"}')
            (folder / 'image.png').write_bytes(b'synthetic-image')
            with patch.object(mount.app, 'all_directories', [directory]), \
                 patch('app.funnel.PUBLIC_HOST', 'checks.local'), \
                 patch.object(fixtures, 'app', PrefixApp(fixtures.app)):
                for suffix in ('test.json', 'test.json/', 'test.json/.', 'test.json/./'):
                    status, body, _ = await fixtures.http('GET', '/test/static/tests/synthetic/' + suffix)
                    self.assertEqual(status, 404)
                    self.assertNotIn('private_answer', body)
                status, _, _ = await fixtures.http('GET', '/test/static/tests/synthetic/image.png')
                self.assertEqual(status, 200)

    async def test_multipart_editor_normalizes_tunnel_media_before_saving(self):
        import json
        boundary = 'synthetic-multipart-editor'
        questions = [{'question_text': 'Synthetic image question', 'question_type': 'single_choice',
                      'image_url': '/test/static/uploads/synthetic.png',
                      'options': [{'option_text': 'Yes', 'is_correct': True,
                                   'image_url': '/test/static/uploads/synthetic.png'},
                                  {'option_text': 'No', 'is_correct': False}]}]
        fields = {'title': 'Synthetic multipart media test', 'questions_json': json.dumps(questions)}
        body = b''
        for name, value in fields.items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
        body += f'--{boundary}--\r\n'.encode()
        status, _, _ = await fixtures.http('POST', '/teacher/tests/create', body,
            cookie=self.teacher_cookie, content_type='multipart/form-data; boundary=' + boundary)
        self.assertEqual(status, 303)
        test = self.db.query(fixtures.models.Test).filter_by(title=fields['title']).one()
        self.assertEqual(test.questions[0].image_url, '/static/uploads/synthetic.png')
        self.assertEqual(test.questions[0].options[0].image_url, '/static/uploads/synthetic.png')

    async def test_assigned_image_alias_is_portable_but_replacing_image_is_blocked(self):
        from fastapi import HTTPException
        from app.services.test_edit_service import validate_editor_questions, preserve_assigned_questions
        from app.services.test_file_service import test_to_dict
        question = self.test.questions[0]
        question.image_url = '/test/static/uploads/assigned.png'
        question.options[0].image_url = '/test/static/uploads/assigned.png'
        self.db.commit()
        data = test_to_dict(self.test)
        self.assertEqual(data['questions'][0]['image_url'], '/static/uploads/assigned.png')
        questions = validate_editor_questions(data['questions'])
        preserve_assigned_questions(self.db, self.test, questions)
        questions[0]['image_url'] = '/static/uploads/replaced.png'
        with self.assertRaises(HTTPException) as error:
            preserve_assigned_questions(self.db, self.test, questions)
        self.assertEqual(error.exception.status_code, 409)

    async def test_security_headers_cover_login_static_and_errors(self):
        for path in ('/teacher/login', '/static/css/styles.css', '/missing-audit-page'):
            with self.subTest(path=path):
                _, _, headers = await fixtures.http('GET', path)
                self.assertEqual(headers[b'x-content-type-options'], b'nosniff')
                self.assertEqual(headers[b'x-frame-options'], b'DENY')
                self.assertEqual(headers[b'referrer-policy'], b'same-origin')


if __name__ == '__main__':
    unittest.main()
