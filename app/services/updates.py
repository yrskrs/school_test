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


def check_updates_available() -> Dict[str, Any]:
    """Check for new versions from GitHub via updater service or directly.
    
    Aggregates changelogs of ALL intermediate releases between current and target versions.
    """
    current_ver = get_current_local_version()

    # 1. First, attempt to call the updater daemon API
    for endpoint in [f"{UPDATER_URL}/check", f"{UPDATER_URL}/api/check"]:
        try:
            req = urllib.request.Request(endpoint, data=b'{}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('ok'):
                    # Ensure intermediate releases filtering is applied
                    latest_ver = data.get('latest_version', current_ver)
                    has_up = data.get('has_update', False)
                    releases = data.get('releases') or []
                    if has_up and not releases and data.get('release'):
                        releases = [data['release']]
                    data['releases'] = releases
                    data['total_intermediate_versions'] = len(releases)
                    return data
        except Exception as exc:
            logger.debug("Updater daemon at %s unreachable: %s", endpoint, exc)

    # 2. Fallback: Query GitHub directly
    repo = getattr(settings, 'GITHUB_REPO', os.getenv('GITHUB_REPO', 'yrskrs/school_test'))
    releases_url = f"https://raw.githubusercontent.com/{repo}/main/RELEASES.json"
    version_url = f"https://raw.githubusercontent.com/{repo}/main/VERSION"
    version_py_url = f"https://raw.githubusercontent.com/{repo}/main/app/version.py"
    changelog_url = f"https://raw.githubusercontent.com/{repo}/main/CHANGELOG.md"

    headers = {'User-Agent': 'SchoolTest-App/1.0'}
    all_releases: List[Dict[str, Any]] = []
    remote_ver = current_ver

    # A. Try fetching RELEASES.json
    try:
        req_rel = urllib.request.Request(releases_url, headers=headers)
        with urllib.request.urlopen(req_rel, timeout=7) as resp_rel:
            all_releases = json.loads(resp_rel.read().decode('utf-8'))
            if all_releases and isinstance(all_releases, list):
                remote_ver = all_releases[0].get('version', current_ver)
    except Exception as e:
        logger.debug("Failed to fetch RELEASES.json from GitHub: %s", e)

    # B. If RELEASES.json failed, try VERSION or app/version.py and CHANGELOG.md
    if not all_releases:
        try:
            req_ver = urllib.request.Request(version_url, headers=headers)
            with urllib.request.urlopen(req_ver, timeout=6) as resp_ver:
                remote_ver = resp_ver.read().decode('utf-8').strip()
        except Exception:
            try:
                req_vpy = urllib.request.Request(version_py_url, headers=headers)
                with urllib.request.urlopen(req_vpy, timeout=6) as resp_vpy:
                    v_text = resp_vpy.read().decode('utf-8')
                    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', v_text)
                    if m:
                        remote_ver = m.group(1).strip()
            except Exception:
                pass

        try:
            req_c = urllib.request.Request(changelog_url, headers=headers)
            with urllib.request.urlopen(req_c, timeout=6) as resp_c:
                c_text = resp_c.read().decode('utf-8')
                all_releases = parse_changelog_all_releases(c_text)
        except Exception:
            pass

    # C. Fallback to local files if both network requests failed
    if not all_releases:
        local_rel_file = ROOT / 'RELEASES.json'
        if local_rel_file.exists():
            try:
                all_releases = json.loads(local_rel_file.read_text(encoding='utf-8'))
            except Exception:
                pass

    has_update = parse_version_tuple(remote_ver) > parse_version_tuple(current_ver)

    # Compute intermediate releases between current and target
    intermediate_releases = filter_intermediate_releases(all_releases, current_ver, remote_ver) if has_update else []
    
    target_release = None
    if intermediate_releases:
        target_release = intermediate_releases[0]
    elif all_releases:
        for r in all_releases:
            if r.get('version') == remote_ver:
                target_release = r
                break
        if not target_release:
            target_release = all_releases[0]
    else:
        target_release = {
            'version': remote_ver,
            'title': f'Оновлення {remote_ver}',
            'date': '',
            'changes': ['Планове вдосконалення функцій та стабільності']
        }

    return {
        'ok': True,
        'current_version': current_ver,
        'latest_version': remote_ver,
        'has_update': has_update,
        'releases': intermediate_releases,
        'total_intermediate_versions': len(intermediate_releases),
        'release': target_release
    }


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
