from scripts.update_metadata import check_remote, local_release
"""Update service: checks for GitHub updates, aggregates release changelogs,
and proxies update/rollback requests to the autonomous updater daemon.
"""
import ast
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from app.config import settings, BASE_DIR

logger = logging.getLogger(__name__)

ROOT = getattr(settings, 'BASE_DIR', BASE_DIR)
STATUS_FILE_BACKUPS = ROOT / 'backups' / 'update_status.json'
STATUS_FILE_DATA = ROOT / 'data' / 'update_status.json'
UPDATER_URL = getattr(settings, 'UPDATER_URL', os.getenv('UPDATER_URL', 'http://updater:8001'))


def get_current_local_version() -> str:
    """Read the current version from VERSION file or app/version.py."""
    version_file = ROOT / 'VERSION'
    if version_file.exists():
        try:
            ver = version_file.read_text(encoding='utf-8').strip()
            if ver:
                return ver
        except Exception:
            pass

    ver_py = ROOT / 'app' / 'version.py'
    if ver_py.exists():
        try:
            tree = ast.parse(ver_py.read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == '__version__':
                            return str(ast.literal_eval(node.value))
        except Exception:
            pass

    return getattr(settings, 'APP_VERSION', '1.5.7')


def parse_version_tuple(v_str: Any) -> Tuple[int, ...]:
    """Parse version string into tuple of integers for comparison, e.g. '1.5.6' -> (1, 5, 6)."""
    try:
        parts = [int(p) for p in re.findall(r'\d+', str(v_str))]
        return tuple(parts) if parts else (0,)
    except Exception:
        return (0,)


def parse_changelog_all_releases(text: str) -> List[Dict[str, Any]]:
    """Parse CHANGELOG.md content into a structured list of releases.
    
    Expected format:
    ## 1.5.6 — 2026-10-11
    
    - Change 1...
    - Change 2...
    """
    releases: List[Dict[str, Any]] = []
    current_rel: Optional[Dict[str, Any]] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = re.match(r'^##\s+([0-9.]+)(?:\s+[—-]\s+([0-9-]+))?(?:\s+[—-]\s+(.+))?', line)
        if m:
            if current_rel:
                releases.append(current_rel)
            version = m.group(1)
            date = m.group(2) or ''
            title = m.group(3) or f"Оновлення {version}"
            current_rel = {
                'version': version,
                'date': date,
                'title': title,
                'changes': []
            }
            continue

        if current_rel and line.startswith('- '):
            change_text = line[2:].strip()
            if change_text:
                current_rel['changes'].append(change_text)

    if current_rel:
        releases.append(current_rel)

    return releases


def filter_intermediate_releases(
    all_releases: List[Dict[str, Any]],
    current_ver: str,
    target_ver: str
) -> List[Dict[str, Any]]:
    """Filter releases strictly between current_ver and target_ver (inclusive of target_ver).
    
    Sorts result descending (newest version first).
    """
    curr_tuple = parse_version_tuple(current_ver)
    target_tuple = parse_version_tuple(target_ver)

    filtered = []
    for rel in all_releases:
        v_tuple = parse_version_tuple(rel.get('version', '0'))
        if curr_tuple < v_tuple <= target_tuple:
            filtered.append(rel)

    # Sort descending by version tuple
    filtered.sort(key=lambda r: parse_version_tuple(r.get('version', '0')), reverse=True)
    return filtered


def check_updates_available():
    current_ver = get_current_local_version()
    payload = json.dumps({'current_version': current_ver}).encode('utf-8')
    try:
        req = urllib.request.Request(f"{UPDATER_URL}/api/check", data=payload,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, dict) and 'ok' in data:
            return data
    except Exception as exc:
        logger.debug("Updater check unavailable: %s", exc)
    return check_remote(ROOT, os.environ.get('GITHUB_REPO', 'yrskrs/school_test'), current_ver)


def get_current_release():
    return local_release(ROOT, get_current_local_version())


def get_update_status() -> Dict[str, Any]:
    """Retrieve live status from updater daemon or fallback to shared status files."""
    # 1. Try updater daemon
    for endpoint in [f"{UPDATER_URL}/status", f"{UPDATER_URL}/api/status"]:
        try:
            req = urllib.request.Request(endpoint, headers={'User-Agent': 'SchoolTest-App/1.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

    # 2. Fallback to status file
    for s_file in [STATUS_FILE_BACKUPS, STATUS_FILE_DATA]:
        if s_file.exists():
            try:
                data = json.loads(s_file.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

    return {
        'state': 'idle',
        'step': 0,
        'step_name': 'Готово до роботи',
        'progress': 0,
        'logs': [],
        'error': None,
        'rolled_back': False,
        'current_version': get_current_local_version()
    }


def start_update(target_ref: str = 'origin/main', target_ver: Optional[str] = None) -> Dict[str, Any]:
    """Trigger update process in the autonomous updater daemon."""
    payload = json.dumps({'ref': target_ref, 'version': target_ver}).encode('utf-8')
    for endpoint in [f"{UPDATER_URL}/update", f"{UPDATER_URL}/api/update"]:
        try:
            req = urllib.request.Request(endpoint, data=payload, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=6) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as he:
            try:
                return json.loads(he.read().decode('utf-8'))
            except Exception:
                return {'ok': False, 'error': f"HTTP {he.code}: {he.reason}"}
        except Exception:
            pass

    return {
        'ok': False,
        'error': "Служба оновлення (updater) тимчасово недоступна. Переконайтеся, що контейнер 'updater' запущено."
    }


def start_rollback(backup_dir: Optional[str] = None) -> Dict[str, Any]:
    """Trigger rollback in the updater daemon."""
    payload = json.dumps({'backup_dir': backup_dir}).encode('utf-8')
    for endpoint in [f"{UPDATER_URL}/rollback", f"{UPDATER_URL}/api/rollback"]:
        try:
            req = urllib.request.Request(endpoint, data=payload, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=6) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as he:
            try:
                return json.loads(he.read().decode('utf-8'))
            except Exception:
                return {'ok': False, 'error': f"HTTP {he.code}: {he.reason}"}
        except Exception:
            pass

    return {
        'ok': False,
        'error': "Служба оновлення (updater) тимчасово недоступна для запуску відновлення."
    }
