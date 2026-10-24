"""Discovery uses the fixture's disposable SQLite/explicit audit database only."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'tests'):
    if str(path) not in sys.path: sys.path.insert(0, str(path))
import test_flows as fixtures
from app import models


class ServiceDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.Flows.asyncSetUp
    asyncTearDown = fixtures.Flows.asyncTearDown

    async def test_ping_is_anonymous_stable_and_has_no_people_or_credentials(self):
        first = await fixtures.http('GET', '/api/v2/ping')
        second = await fixtures.http('GET', '/api/v2/ping/')
        self.assertEqual(first[0], 200)
        payload = json.loads(first[1])
        self.assertEqual(payload, json.loads(second[1]))
        self.assertEqual(set(payload), {'source', 'instance_id', 'version'})
        self.assertEqual(payload['source'], 'schooltest')
        self.assertLess(len(first[1]), 256)
        self.assertNotIn(self.owner.username, first[1])
        self.assertEqual((await fixtures.http('POST', '/api/v2/ping'))[0], 405)

    async def test_authenticated_metadata_never_returns_key_or_students(self):
        self.db.add(models.GradeExportGrant(teacher_id=self.owner.id,
            token_hash=hashlib.sha256(b'synthetic-discovery-key').hexdigest(), active=True))
        self.db.commit()
        self.assertEqual((await fixtures.http('GET', '/api/v2/journal/connection/'))[0], 401)
        response = await fixtures.http('GET', '/api/v2/journal/connection/', authorization='Bearer synthetic-discovery-key')
        self.assertEqual(response[0], 200)
        payload = json.loads(response[1])
        self.assertEqual(payload['permissions'], {'export': True, 'roster_import': True})
        self.assertNotIn('students', payload)
        self.assertNotIn('synthetic-discovery-key', response[1])
        self.assertNotIn(self.owner.username, response[1])
