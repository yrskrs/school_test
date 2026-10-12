#!/usr/bin/env python3
"""Local updater service: executes updates, reports progress, and performs automatic rollbacks."""
import ast
import http.server
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parent.parent))
STATUS_FILE_BACKUPS = ROOT / 'backups' / 'update_status.json'
STATUS_FILE_DATA = ROOT / 'data' / 'update_status.json'
LOCK_FILE = ROOT / '.updater.lock'

CURRENT_STATE: Dict[str, Any] = {
    'state': 'idle',  # 'idle', 'running', 'success', 'error'
    'step': 0,
    'step_name': 'Готово до роботи',
    'progress': 0,
    'logs': [],
    'error': None,
    'rolled_back': False,
    'target_version': None,
    'current_version': '1.5.7',
    'releases': [],
    'backup_dir': None,
    'started_at': None,
    'updated_at': None,
}

STATE_LOCK = threading.Lock()


def get_current_version() -> str:
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
    return '1.5.7'


def parse_version_tuple(v_str: Any) -> Tuple[int, ...]:
    try:
        parts = [int(p) for p in re.findall(r'\d+', str(v_str))]
        return tuple(parts) if parts else (0,)
    except Exception:
        return (0,)


def save_status():
    payload = dict(CURRENT_STATE)
    for s_file in [STATUS_FILE_BACKUPS, STATUS_FILE_DATA]:
        try:
            s_file.parent.mkdir(parents=True, exist_ok=True)
            with open(s_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def update_state(**kwargs):
    with STATE_LOCK:
        CURRENT_STATE.update(kwargs)
        CURRENT_STATE['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save_status()


def append_log(line: str):
    clean_line = line.strip()
    if not clean_line:
        return
    timestamp = time.strftime('%H:%M:%S')
    log_entry = f"[{timestamp}] {clean_line}"
    print(log_entry, flush=True)
    with STATE_LOCK:
        CURRENT_STATE['logs'].append(log_entry)
        if len(CURRENT_STATE['logs']) > 400:
            CURRENT_STATE['logs'] = CURRENT_STATE['logs'][-400:]
        CURRENT_STATE['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save_status()


def parse_changelog_all_releases(text: str) -> List[Dict[str, Any]]:
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
    curr_tuple = parse_version_tuple(current_ver)
    target_tuple = parse_version_tuple(target_ver)

    filtered = []
    for rel in all_releases:
        v_tuple = parse_version_tuple(rel.get('version', '0'))
        if curr_tuple < v_tuple <= target_tuple:
            filtered.append(rel)

    filtered.sort(key=lambda r: parse_version_tuple(r.get('version', '0')), reverse=True)
    return filtered


def check_github_updates() -> Dict[str, Any]:
    current_ver = get_current_version()
    repo = os.environ.get('GITHUB_REPO', 'yrskrs/school_test')
    releases_url = f"https://raw.githubusercontent.com/{repo}/main/RELEASES.json"
    version_url = f"https://raw.githubusercontent.com/{repo}/main/VERSION"
    version_py_url = f"https://raw.githubusercontent.com/{repo}/main/app/version.py"
    changelog_url = f"https://raw.githubusercontent.com/{repo}/main/CHANGELOG.md"

    headers = {'User-Agent': 'SchoolTest-Updater/1.0'}
    all_releases: List[Dict[str, Any]] = []
    remote_ver = current_ver
    remote_commit = ''
    local_commit = ''

    # 1. Try RELEASES.json
    try:
        req_rel = urllib.request.Request(releases_url, headers=headers)
        with urllib.request.urlopen(req_rel, timeout=7) as resp_rel:
            all_releases = json.loads(resp_rel.read().decode('utf-8'))
            if all_releases and isinstance(all_releases, list):
                remote_ver = all_releases[0].get('version', current_ver)
    except Exception:
        pass

    # 2. If RELEASES.json failed, try VERSION and CHANGELOG.md
    if not all_releases:
        try:
            req_v = urllib.request.Request(version_url, headers=headers)
            with urllib.request.urlopen(req_v, timeout=6) as resp_v:
                remote_ver = resp_v.read().decode('utf-8').strip()
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

    # 3. Fallback to local files if network failed
    if not all_releases:
        local_rel_file = ROOT / 'RELEASES.json'
        if local_rel_file.exists():
            try:
                all_releases = json.loads(local_rel_file.read_text(encoding='utf-8'))
            except Exception:
                pass

    # 4. Check git commit hash comparison if in git repo
    try:
        loc_p = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT), capture_output=True, text=True)
        if loc_p.returncode == 0:
            local_commit = loc_p.stdout.strip()
        git_env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
        ls_p = subprocess.run(['git', 'ls-remote', f"https://github.com/{repo}.git", 'refs/heads/main'], cwd=str(ROOT), env=git_env, capture_output=True, text=True, timeout=5)
        if ls_p.returncode == 0 and ls_p.stdout:
            remote_commit = ls_p.stdout.split()[0]
    except Exception:
        pass

    has_update = parse_version_tuple(remote_ver) > parse_version_tuple(current_ver)
    if not has_update and remote_commit and local_commit and remote_commit != local_commit:
        has_update = True

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
            'date': time.strftime('%Y-%m-%d'),
            'changes': ['Планове оновлення системи']
        }

    return {
        'ok': True,
        'current_version': current_ver,
        'latest_version': remote_ver,
        'has_update': has_update,
        'releases': intermediate_releases,
        'total_intermediate_versions': len(intermediate_releases),
        'remote_commit': remote_commit[:10] if remote_commit else '',
        'local_commit': local_commit[:10] if local_commit else '',
        'release': target_release
    }


def execute_update_job(target_ref='origin/main', target_ver=None, releases_list=None):
    current_ver = get_current_version()
    
    # Pre-calculate releases if not provided
    if not releases_list:
        try:
            check_data = check_github_updates()
            releases_list = check_data.get('releases') or ([check_data['release']] if check_data.get('release') else [])
        except Exception:
            releases_list = []

    update_state(
        state='running',
        step=1,
        step_name='Отримання коду з GitHub',
        progress=15,
        logs=[],
        error=None,
        rolled_back=False,
        target_version=target_ver or 'останньої версії',
        current_version=current_ver,
        releases=releases_list,
        started_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    )
    append_log(f"Розпочато процес оновлення з {current_ver} до {target_ver or target_ref}...")

    backup_dir = None
    old_image = None
    rollback_tag = None
    app_was_running = False
    candidate_built = False
    target_commit = None

    try:
        # Ensure git safe.directory
        subprocess.run(['git', 'config', '--global', '--add', 'safe.directory', '*'], check=False)

        # ── Step 1: Fetch code from GitHub ────────────────────────────────────
        append_log("Крок 1/5: Отримання останнього коду з репозиторію GitHub...")
        fetch_proc = subprocess.run(
            ['python3', str(ROOT / 'scripts' / 'fetch_update.py'), '--branch', 'main'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90
        )
        if fetch_proc.returncode != 0:
            err = fetch_proc.stderr or fetch_proc.stdout or 'Помилка git fetch'
            raise RuntimeError(f"Не вдалося отримати оновлення з репозиторію: {err}")
        append_log("Код успішно завантажено з GitHub.")

        # Resolve target commit
        try:
            target_commit = subprocess.check_output(
                ['git', 'rev-parse', '--verify', f"{target_ref}^{{commit}}"],
                cwd=str(ROOT),
                text=True
            ).strip()
            current_commit = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'],
                cwd=str(ROOT),
                text=True
            ).strip()
            append_log(f"Поточний коміт: {current_commit[:10]}, Цільовий коміт: {target_commit[:10]}")
        except Exception:
            target_commit = 'origin/main'

        # Check merge-base to ensure no divergence
        diverge_chk = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', 'HEAD', 'origin/main'],
            cwd=str(ROOT),
            capture_output=True,
            text=True
        )
        if diverge_chk.returncode != 0:
            raise RuntimeError("Локальна гілка має невідправлені зміни, що розходяться з origin/main; автоматичне злиття скасовано для безпеки даних.")

        # ── Step 2: Build candidate in isolated git worktree ───────────────────
        update_state(step=2, step_name='Збірка образу кандидата у тимчасовому worktree', progress=35)
        append_log("Крок 2/5: Збірка Docker-образу кандидата у тимчасовому git worktree...")

        worktree_dir = Path(f"/tmp/schooltest-build.{target_commit[:8] if isinstance(target_commit, str) else 'tmp'}")
        if worktree_dir.exists():
            subprocess.run(['rm', '-rf', str(worktree_dir)], check=False)

        worktree_created = False
        try:
            # Create detached worktree at target_commit
            wt_add = subprocess.run(
                ['git', 'worktree', 'add', '--detach', str(worktree_dir), str(target_commit)],
                cwd=str(ROOT),
                capture_output=True,
                text=True
            )
            if wt_add.returncode == 0:
                worktree_created = True
                append_log(f"Тимчасове робоче дерево створено в {worktree_dir.name}.")
                candidate_image = f"schooltest5-candidate:{target_commit[:10]}"
                build_proc = subprocess.run(
                    ['docker', 'build', '--build-arg', f'VCS_REF={target_commit}', '-t', candidate_image, str(worktree_dir)],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if build_proc.returncode != 0:
                    raise RuntimeError(f"Помилка збірки образу кандидата: {build_proc.stderr or build_proc.stdout}")
                candidate_built = True
                append_log(f"Docker-образ кандидата {candidate_image} успішно зібрано.")
            else:
                append_log("Попередження: створення worktree не підтримується або коміт уже злито, буде використано звичайну збірку.")
        finally:
            if worktree_created:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(worktree_dir)], cwd=str(ROOT), check=False)

        # ── Step 3: Quiesced backup and safety tagging ────────────────────────
        update_state(step=3, step_name='Створення резервної копії та тегування образу', progress=55)
        append_log("Крок 3/5: Підготовка повної резервної копії бази даних, файлів та образу...")

        ps_proc = subprocess.run(['docker', 'compose', 'ps', '-aq', 'app'], cwd=str(ROOT), capture_output=True, text=True)
        container_id = ps_proc.stdout.strip()
        if container_id:
            run_chk = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', container_id], cwd=str(ROOT), capture_output=True, text=True)
            if run_chk.stdout.strip().lower() == 'true':
                app_was_running = True
            img_chk = subprocess.run(['docker', 'inspect', '--format', '{{.Image}}', container_id], cwd=str(ROOT), capture_output=True, text=True)
            old_image = img_chk.stdout.strip()

        backup_dir = ROOT / 'backups' / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}-update"
        backup_dir.mkdir(parents=True, exist_ok=True)
        update_state(backup_dir=str(backup_dir))

        if old_image:
            rollback_tag = f"schooltest-rollback:{backup_dir.name}"
            subprocess.run(['docker', 'image', 'tag', old_image, rollback_tag], cwd=str(ROOT), check=False)
            append_log(f"Попередній образ позначено резервним тегом: {rollback_tag}")

        append_log(f"Створення знімка даних у {backup_dir.name}...")
        backup_proc = subprocess.run(
            ['bash', str(ROOT / 'scripts' / 'backup.sh'), '--output', str(backup_dir), '--keep-stopped'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180
        )
        if backup_proc.returncode != 0:
            err = backup_proc.stderr or backup_proc.stdout or 'Помилка backup.sh'
            raise RuntimeError(f"Не вдалося створити обов'язкову резервну копію: {err}")
        append_log(f"Резервну копію успішно створено та перевірено ({backup_dir.name}).")

        # ── Step 4: Merge code and restart app container ──────────────────────
        update_state(step=4, step_name='Застосування нової версії коду та перезапуск сервісу', progress=75)
        append_log("Крок 4/5: Застосування нової версії коду (Git merge)...")

        merge_proc = subprocess.run(
            ['git', 'merge', '--ff-only', str(target_commit)],
            cwd=str(ROOT),
            capture_output=True,
            text=True
        )
        if merge_proc.returncode != 0:
            raise RuntimeError(f"Помилка злиття коду: {merge_proc.stderr or merge_proc.stdout}")

        # Update .env image tag
        subprocess.run(['python3', str(ROOT / 'scripts' / 'select_release_image.py'), 'schooltest5'], cwd=str(ROOT), check=False)
        new_installed_ver = get_current_version()
        append_log(f"Код оновлено до версії {new_installed_ver}.")

        # If not pre-built in worktree, build now
        if not candidate_built:
            append_log("Збірка образу Docker для оновленого коду...")
            build_proc = subprocess.run(
                ['docker', 'compose', 'build', 'app'],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=600
            )
            if build_proc.returncode != 0:
                raise RuntimeError(f"Помилка збірки образу Docker: {build_proc.stderr or build_proc.stdout}")

        # Restore uploads if archive exists
        files_tar = backup_dir / 'files.tar.gz'
        if files_tar.exists():
            try:
                restore_code = (ROOT / 'scripts' / 'backup_bundle.py').read_text(encoding='utf-8')
                subprocess.run(
                    ['docker', 'compose', 'run', '--rm', '--no-deps', '-T', 'app', 'python', '-c', restore_code, 'restore-uploads'],
                    input=files_tar.read_bytes(),
                    cwd=str(ROOT),
                    capture_output=True,
                    check=False
                )
            except Exception as e:
                append_log(f"Попередження при відновленні uploads: {e}")

        append_log("Перезапуск сервісу з новим образом...")
        up_proc = subprocess.run(
            ['docker', 'compose', 'up', '-d', '--no-deps', 'app'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120
        )
        if up_proc.returncode != 0:
            raise RuntimeError(f"Помилка запуску контейнера: {up_proc.stderr or up_proc.stdout}")

        # ── Step 5: Healthcheck verification ──────────────────────────────────
        update_state(step=5, step_name='Перевірка працездатності системи (/healthz)', progress=90)
        append_log("Крок 5/5: Перевірка працездатності застосунку (/healthz)...")

        is_healthy = False
        app_url = os.environ.get('APP_URL', 'http://app:8000')
        fallback_urls = [app_url, 'http://127.0.0.1:8080', 'http://127.0.0.1:8000']

        for attempt in range(40):
            time.sleep(2)
            for test_url in fallback_urls:
                try:
                    req_h = urllib.request.Request(f"{test_url}/healthz", headers={'User-Agent': 'SchoolTest-Updater/1.0'})
                    with urllib.request.urlopen(req_h, timeout=3) as resp_h:
                        if resp_h.status == 200:
                            data = json.loads(resp_h.read().decode('utf-8'))
                            if data.get('status') == 'ok':
                                is_healthy = True
                                break
                except Exception:
                    pass
            if is_healthy:
                break
            if attempt % 5 == 0 and attempt > 0:
                append_log(f"Очікування готовності сервісу (спроба {attempt}/40)...")

        if not is_healthy:
            raise RuntimeError("Сервіс не пройшов перевірку готовності (/healthz) після оновлення.")

        # Re-configure integration network if script exists
        try:
            subprocess.run(['python3', str(ROOT / 'scripts' / 'configure_integration_network.py')], cwd=str(ROOT), capture_output=True, check=False)
        except Exception:
            pass

        final_ver = get_current_version()
        append_log(f"✅ Оновлення успішно завершено! Поточна версія сайту: {final_ver}")
        update_state(
            state='success',
            step=5,
            step_name='Оновлення успішно завершено',
            progress=100,
            target_version=final_ver,
            current_version=final_ver,
            error=None
        )

    except Exception as exc:
        err_msg = str(exc)
        append_log(f"❌ ПОМИЛКА ОНОВЛЕННЯ: {err_msg}")
        update_state(
            state='error',
            error=err_msg,
            step_name='Помилка під час оновлення'
        )

        # Automatic rollback
        if backup_dir and backup_dir.exists():
            append_log(f"⚠️ АВТОМАТИЧНЕ ВІДНОВЛЕННЯ: Повернення до резервної копії {backup_dir.name}...")
            try:
                if rollback_tag and old_image:
                    subprocess.run(['docker', 'image', 'tag', rollback_tag, old_image], cwd=str(ROOT), check=False)

                restore_proc = subprocess.run(
                    ['bash', str(ROOT / 'scripts' / 'restore.sh'), str(backup_dir), '--yes'],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=240
                )
                if restore_proc.returncode == 0:
                    append_log("✅ Попередній стабільний стан системи успішно відновлено!")
                    update_state(
                        rolled_back=True,
                        step_name="Оновлення не вдалося. Систему автоматично повернуто до попередньої версії."
                    )
                else:
                    append_log(f"Помилка відновлення: {restore_proc.stderr or restore_proc.stdout}")
            except Exception as r_exc:
                append_log(f"Критична помилка при відновленні: {r_exc}")
        elif app_was_running:
            subprocess.run(['docker', 'compose', 'start', 'app'], cwd=str(ROOT), check=False)


def execute_rollback_job(backup_dir_path=None):
    update_state(state='running', step_name='Відновлення попередньої версії...', progress=30)
    target = backup_dir_path or CURRENT_STATE.get('backup_dir')
    if not target or not Path(target).exists():
        backups = sorted([p for p in (ROOT / 'backups').iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        if backups:
            target = str(backups[0])

    if not target or not Path(target).exists():
        update_state(state='error', error='Резервну копію для відновлення не знайдено.')
        append_log("Помилка: резервну копію для відновлення не знайдено.")
        return

    append_log(f"Запуск ручного відновлення з {Path(target).name}...")
    try:
        proc = subprocess.run(
            ['bash', str(ROOT / 'scripts' / 'restore.sh'), str(target), '--yes'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300
        )
        if proc.returncode == 0:
            append_log("✅ Систему успішно відновлено з резервної копії.")
            current_ver = get_current_version()
            update_state(
                state='idle',
                progress=100,
                step=0,
                step_name='Систему відновлено',
                rolled_back=True,
                error=None,
                current_version=current_ver
            )
        else:
            append_log(f"Помилка відновлення: {proc.stderr or proc.stdout}")
            update_state(state='error', error=f"Помилка відновлення: {proc.stderr or proc.stdout}")
    except Exception as e:
        append_log(f"Критична помилка відновлення: {e}")
        update_state(state='error', error=str(e))


class UpdaterHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health', '/healthz'):
            self._send_json(200, {'status': 'ok', 'service': 'schooltest-updater'})
        elif self.path in ('/status', '/api/status'):
            with STATE_LOCK:
                st = dict(CURRENT_STATE)
            st['current_version'] = get_current_version()
            self._send_json(200, st)
        elif self.path in ('/check', '/api/check'):
            result = check_github_updates()
            self._send_json(200, result)
        else:
            self._send_json(404, {'error': 'Not found'})

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len).decode('utf-8') if content_len > 0 else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if self.path in ('/check', '/api/check'):
            result = check_github_updates()
            self._send_json(200, result)

        elif self.path in ('/update', '/api/update'):
            with STATE_LOCK:
                if CURRENT_STATE.get('state') == 'running':
                    self._send_json(409, {'error': 'Оновлення вже виконується'})
                    return
            target_ref = data.get('ref', 'origin/main')
            target_ver = data.get('version')
            releases_list = data.get('releases')
            thread = threading.Thread(target=execute_update_job, args=(target_ref, target_ver, releases_list), daemon=True)
            thread.start()
            self._send_json(200, {'ok': True, 'message': 'Оновлення розпочато', 'target_version': target_ver})

        elif self.path in ('/rollback', '/api/rollback'):
            target_backup = data.get('backup_dir')
            thread = threading.Thread(target=execute_rollback_job, args=(target_backup,), daemon=True)
            thread.start()
            self._send_json(200, {'ok': True, 'message': 'Відновлення розпочато'})

        else:
            self._send_json(404, {'error': 'Not found'})

    def _send_json(self, status, payload):
        text = json.dumps(payload, ensure_ascii=False)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(text.encode('utf-8'))

    def log_message(self, format, *args):
        pass


def run_server(port=8001):
    CURRENT_STATE['current_version'] = get_current_version()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('0.0.0.0', port), UpdaterHTTPHandler) as httpd:
        print(f"SchoolTest Updater Service listening on 0.0.0.0:{port}...", flush=True)
        save_status()
        httpd.serve_forever()


if __name__ == '__main__':
    port = int(os.environ.get('UPDATER_PORT', 8001))
    run_server(port)
