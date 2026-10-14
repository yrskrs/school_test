"""Automated test suite for the System Updates functionality:
- Version comparison and multi-version changelog parsing
- Intermediate releases aggregation (e.g. 1.2.0 -> 1.2.3)
- Network error handling & fallback to local RELEASES/CHANGELOG
- Strict role-based permissions (admin only, regular teachers get 403, unauthenticated get 401/redirect)
- Updates API endpoints (/teacher/updates, /api/check, /api/status, /api/start, /api/rollback)

Run: .venv/bin/python tests/test_updates.py
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORK = tempfile.TemporaryDirectory(prefix="schooltest-updates-")
os.environ["DATABASE_URL"] = f"sqlite:///{WORK.name}/checks.db"
os.environ["SECRET_KEY"] = "synthetic-updates-key"
os.environ["DEBUG"] = "False"
os.environ["ROSTER_PEERS"] = "[]"
os.chdir(WORK.name)

from app.config import settings
settings.ROSTER_PEERS = []
from app.main import app
from app import crud, models
from app.database import Base, SessionLocal, engine
from app.security import create_teacher_session
from app.services.updates import (
    check_updates_available,
    filter_intermediate_releases,
    get_current_local_version,
    get_update_status,
    parse_changelog_all_releases,
    parse_version_tuple,
    start_rollback,
    start_update,
)
from fastapi.responses import Response


def cookie_for(function, *args):
    response = Response()
    function(response, *args)
    return response.headers["set-cookie"].split(";", 1)[0]


async def http(method, path, data=None, cookie=None, form=False, content_type=None, accept="text/html"):
    headers = [(b"host", b"checks.local"), (b"accept", accept.encode())]
    payload = b""
    if data is not None:
        payload = data if isinstance(data, bytes) else (urlencode(data).encode() if form else json.dumps(data).encode())
        headers.append((b"content-type", content_type.encode() if content_type else (b"application/x-www-form-urlencoded" if form else b"application/json")))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    path, _, query = path.partition("?")
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
        "headers": headers, "client": ("127.0.0.1", 1), "server": ("checks.local", 80)
    }
    messages, consumed = [], False
    completed = asyncio.Event()

    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await completed.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            completed.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body").decode()
    return start["status"], body, dict(start["headers"])


class TestVersionAndChangelogParsing(unittest.TestCase):
    """Unit tests for version parsing and multi-version changelog aggregation."""

    def test_published_version_and_release_metadata_match_app(self):
        from app.version import __version__
        self.assertEqual((ROOT / 'VERSION').read_text().strip(), __version__)
        releases = json.loads((ROOT / 'RELEASES.json').read_text())
        self.assertEqual(releases[0]['version'], __version__)
        self.assertTrue(releases[0]['changes'])

    def test_parse_version_tuple(self):
        self.assertEqual(parse_version_tuple("1.5.6"), (1, 5, 6))
        self.assertEqual(parse_version_tuple("v1.2.0"), (1, 2, 0))
        self.assertEqual(parse_version_tuple("2.0.1.4"), (2, 0, 1, 4))
        self.assertEqual(parse_version_tuple("invalid"), (0,))
        self.assertTrue(parse_version_tuple("1.5.6") > parse_version_tuple("1.5.0"))
        self.assertTrue(parse_version_tuple("1.5.10") > parse_version_tuple("1.5.9"))
        self.assertTrue(parse_version_tuple("2.0.0") > parse_version_tuple("1.9.9"))

    def test_parse_changelog_all_releases(self):
        sample_md = """# Changelog
## 1.2.3 — 2026-10-12 — Безпека та виправлення
- Виправлено перевірку токенів
- Додано нові тести

## 1.2.2 — 2026-10-11
- Оновлено базу даних
- Покращено інтерфейс

## 1.2.1 — 2026-10-10 — Початкові покращення
- Перше виправлення
"""
        releases = parse_changelog_all_releases(sample_md)
        self.assertEqual(len(releases), 3)
        self.assertEqual(releases[0]["version"], "1.2.3")
        self.assertEqual(releases[0]["date"], "2026-10-12")
        self.assertEqual(releases[0]["title"], "Безпека та виправлення")
        self.assertEqual(releases[0]["changes"], ["Виправлено перевірку токенів", "Додано нові тести"])

        self.assertEqual(releases[1]["version"], "1.2.2")
        self.assertEqual(releases[1]["title"], "Оновлення 1.2.2")
        self.assertEqual(len(releases[1]["changes"]), 2)

    def test_filter_intermediate_releases_multi(self):
        """Verify grouping of ALL versions between current and target (e.g. 1.2.0 to 1.2.3)."""
        all_releases = [
            {"version": "1.3.0", "title": "Future", "changes": ["Future change"]},
            {"version": "1.2.3", "title": "Patch 3", "changes": ["Fix 3"]},
            {"version": "1.2.2", "title": "Patch 2", "changes": ["Fix 2"]},
            {"version": "1.2.1", "title": "Patch 1", "changes": ["Fix 1"]},
            {"version": "1.2.0", "title": "Base", "changes": ["Base"]},
            {"version": "1.1.0", "title": "Old", "changes": ["Old"]},
        ]

        # Current is 1.2.0, updating to 1.2.3: should include 1.2.3, 1.2.2, 1.2.1
        filtered = filter_intermediate_releases(all_releases, current_ver="1.2.0", target_ver="1.2.3")
        self.assertEqual(len(filtered), 3)
        self.assertEqual([r["version"] for r in filtered], ["1.2.3", "1.2.2", "1.2.1"])
        self.assertEqual(filtered[0]["changes"], ["Fix 3"])
        self.assertEqual(filtered[1]["changes"], ["Fix 2"])
        self.assertEqual(filtered[2]["changes"], ["Fix 1"])

    def test_filter_intermediate_releases_up_to_date(self):
        all_releases = [
            {"version": "1.5.6", "title": "Latest", "changes": ["Latest change"]},
            {"version": "1.5.5", "title": "Previous", "changes": ["Prev change"]},
        ]
        # Current is 1.5.6, target is 1.5.6 -> 0 intermediate releases
        filtered = filter_intermediate_releases(all_releases, current_ver="1.5.6", target_ver="1.5.6")
        self.assertEqual(len(filtered), 0)


class TestUpdatesService(unittest.TestCase):
    """Unit tests for check_updates_available, network mocking, and error handling."""

    @patch("app.services.updates.get_current_local_version", return_value="1.5.6")
    @patch("urllib.request.urlopen")
    def test_check_updates_with_releases_json(self, mock_urlopen, mock_current_ver):
        sample_releases = [
            {
                "version": "1.6.0",
                "date": "2026-10-15",
                "title": "Нові можливості",
                "changes": ["Додано експорт", "Оновлено безпеку"]
            },
            {
                "version": "1.5.7",
                "date": "2026-10-13",
                "title": "Патч стабільності",
                "changes": ["Виправлено помилку 500"]
            }
        ]

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_releases).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        def response(req, timeout=None):
            if req.full_url.endswith('VERSION'):
                version_resp = MagicMock()
                version_resp.read.return_value = b'1.6.0'
                version_resp.__enter__.return_value = version_resp
                return version_resp
            return mock_resp
        mock_urlopen.side_effect = response

        result = check_updates_available()
        self.assertTrue(result["ok"])
        self.assertTrue(result["has_update"])
        self.assertEqual(result["latest_version"], "1.6.0")
        # Since current is mocked as 1.5.6, both 1.6.0 and 1.5.7 are intermediate
        self.assertEqual(len(result["releases"]), 2)
        self.assertEqual(result["releases"][0]["version"], "1.6.0")
        self.assertEqual(result["releases"][1]["version"], "1.5.7")

    @patch("urllib.request.urlopen")
    def test_check_updates_network_failure_falls_back_gracefully(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection timed out")
        result = check_updates_available()
        # Should not raise exception and should return a valid result
        self.assertFalse(result["ok"])
        self.assertIn("current_version", result)
        self.assertIn("has_update", result)

    @patch("urllib.request.urlopen")
    def test_start_update_daemon_call(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "message": "Оновлення розпочато"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = start_update(target_ref="origin/main", target_ver="1.6.0")
        self.assertTrue(res.get("ok"))

    @patch("urllib.request.urlopen")
    def test_start_rollback_daemon_call(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "message": "Відновлення розпочато"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = start_rollback(backup_dir="/workspace/backups/test-backup")
        self.assertTrue(res.get("ok"))


class TestUpdatesRoutesAndPermissions(unittest.IsolatedAsyncioTestCase):
    """Integration tests for route permissions, HTTP codes, and responses."""

    async def asyncSetUp(self):
        async def ticker():
            while True:
                await asyncio.sleep(.01)
        self.ticker = asyncio.create_task(ticker())
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

        # Seed admin teacher and regular teacher
        self.db = SessionLocal()
        self.admin = crud.create_teacher(
            self.db, username="admin", full_name="Суперадмін",
            password="secretpassword", subject="Інформатика", classes="10-А"
        )
        self.regular_teacher = crud.create_teacher(
            self.db, username="kovalenko", full_name="Олена Коваленко",
            password="secretpassword", subject="Математика", classes="11-Б"
        )
        self.admin_cookie = cookie_for(create_teacher_session, self.admin.id, self.admin.username)
        self.teacher_cookie = cookie_for(create_teacher_session, self.regular_teacher.id, self.regular_teacher.username)

    async def asyncTearDown(self):
        self.ticker.cancel()
        try:
            await self.ticker
        except asyncio.CancelledError:
            pass
        self.db.close()

    async def test_unauthenticated_access_is_rejected(self):
        # HTML request should redirect or 401
        status, body, headers = await http("GET", "/teacher/updates", accept="text/html")
        self.assertIn(status, (303, 401))

        # API check without cookie should reject
        status_api, _, _ = await http("GET", "/teacher/updates/api/check", accept="application/json")
        self.assertIn(status_api, (303, 401))

    async def test_regular_teacher_forbidden(self):
        """A regular teacher must be forbidden (HTTP 403) from accessing updates."""
        status_page, body, _ = await http("GET", "/teacher/updates", cookie=self.teacher_cookie)
        self.assertEqual(status_page, 403)
        self.assertIn("Доступ заборонено", body)

        status_check, _, _ = await http("GET", "/teacher/updates/api/check", cookie=self.teacher_cookie, accept="application/json")
        self.assertEqual(status_check, 403)

        status_start, _, _ = await http("POST", "/teacher/updates/api/start", data={"version": "1.6.0"}, cookie=self.teacher_cookie, accept="application/json")
        self.assertEqual(status_start, 403)

        status_status, _, _ = await http("GET", "/teacher/updates/api/status", cookie=self.teacher_cookie, accept="application/json")
        self.assertEqual(status_status, 403)

        status_rollback, _, _ = await http("POST", "/teacher/updates/api/rollback", data={}, cookie=self.teacher_cookie, accept="application/json")
        self.assertEqual(status_rollback, 403)

    async def test_installation_owner_can_update_when_named_admin_is_absent(self):
        self.admin.username = 'installation-owner'
        self.db.commit()
        owner_cookie = cookie_for(create_teacher_session, self.admin.id, self.admin.username)
        status_page, body, _ = await http('GET', '/teacher/updates', cookie=owner_cookie)
        self.assertEqual(status_page, 200)
        self.assertIn('href="/teacher/updates"', body)
        status_other, _, _ = await http('GET', '/teacher/updates', cookie=self.teacher_cookie)
        self.assertEqual(status_other, 403)

    async def test_admin_access_granted(self):
        """Admin teacher must receive HTTP 200 on the updates page and status API."""
        status_page, body, _ = await http("GET", "/teacher/updates", cookie=self.admin_cookie)
        self.assertEqual(status_page, 200)
        self.assertIn("Оновлення системи", body)
        self.assertIn("Поточна встановлена версія", body)
        self.assertIn("Журнал виконання оновлення", body)
        self.assertIn("⚡ Перезібрати поточну версію", body)
        self.assertIn("Опис встановленої версії", body)
        self.assertIn("Скопіювати помилку та журнал", body)
        self.assertEqual(body.count('onclick="checkUpdates()"'), 1)
        self.assertIn('<details id="updateProgressSection"', body)
        self.assertNotIn('initialCheckPrompt', body)

        status_status, body_status, _ = await http("GET", "/teacher/updates/api/status", cookie=self.admin_cookie, accept="application/json")
        self.assertEqual(status_status, 200)
        data = json.loads(body_status)
        self.assertIn("state", data)
        self.assertIn("step", data)

    @patch("urllib.request.urlopen")
    async def test_admin_check_api_endpoint(self, mock_urlopen):
        sample_releases = [
            {"version": "1.6.0", "title": "Нові можливості", "changes": ["Додано експорт"]}
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_releases).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        def response(req, timeout=None):
            if req.full_url.endswith('VERSION'):
                version_resp = MagicMock()
                version_resp.read.return_value = b'1.6.0'
                version_resp.__enter__.return_value = version_resp
                return version_resp
            return mock_resp
        mock_urlopen.side_effect = response

        status_check, body_check, _ = await http("GET", "/teacher/updates/api/check", cookie=self.admin_cookie, accept="application/json")
        self.assertEqual(status_check, 200)
        data = json.loads(body_check)
        self.assertTrue(data.get("ok"))
        self.assertIn("current_version", data)

    @patch("urllib.request.urlopen")
    async def test_admin_start_update_api_endpoint(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "message": "Оновлення розпочато"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        status, body, _ = await http("POST", "/teacher/updates/api/start", data={"version": "1.6.0"}, cookie=self.admin_cookie, accept="application/json")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data.get("ok"))

    @patch("urllib.request.urlopen")
    async def test_admin_rollback_api_endpoint(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "message": "Відновлення розпочато"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        status, body, _ = await http("POST", "/teacher/updates/api/rollback", data={}, cookie=self.admin_cookie, accept="application/json")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data.get("ok"))

    async def test_updates_redirect(self):
        status, _, headers = await http("GET", "/updates")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get(b"location", b"").decode(), "/teacher/updates")


if __name__ == "__main__":
    unittest.main()
