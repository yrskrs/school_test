"""Maintenance control-flow tests with disposable files and fake Docker/Git.

Run: .venv/bin/python tests/test_maintenance.py. Never contacts Docker or GitHub.
"""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle', ROOT / 'scripts/backup_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

FAKE = r'''#!/usr/bin/env python3
import io, json, os, pathlib, sys, tarfile
args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
log = pathlib.Path(os.environ['MOCK_LOG'])
with log.open('a') as f:
    f.write(json.dumps([name] + args) + '\n')
failure = os.environ.get('MOCK_FAIL', '')
joined = ' '.join(args)
if failure and failure in joined:
    sys.exit(1)
if name == 'git':
    if args == ['rev-parse', '--show-toplevel']: print(os.environ['MOCK_ROOT'])
    elif args == ['branch', '--show-current']: print('main')
    elif args == ['rev-parse', 'HEAD']: print('synthetic-commit')
    elif args == ['status', '--porcelain']: print(os.environ.get('MOCK_DIRTY', ''), end='')
    sys.exit(0)
if name == 'sleep': sys.exit(0)
if args and args[0] == 'inspect':
    print('sha256:synthetic-image' if '.Image' in joined else 'true')
    sys.exit(0)
if args[:2] == ['compose', 'ps']:
    print('synthetic-container')
    sys.exit(0)
if args[:2] == ['compose', 'cp']:
    target = pathlib.Path(args[-1]); target.mkdir(parents=True)
    (target / 'sample.txt').write_text('synthetic-file')
    sys.exit(0)
if args[:2] == ['compose', 'exec']:
    if 'pg_dump' in joined: sys.stdout.buffer.write(b'PGDMPsynthetic-dump')
    if 'psql' in joined or 'pg_restore' in joined: sys.stdin.buffer.read()
    sys.exit(0)
if args[:2] == ['compose', 'run']:
    # Drain input; filesystem semantics are tested separately with real archives.
    sys.stdin.buffer.read()
    sys.exit(0)
sys.exit(0)
'''


def archive_bytes(uploads=True):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for root in ('data', 'tests', 'uploads') if uploads else ('data', 'tests'):
            directory = tarfile.TarInfo(root)
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            content = b'snapshot'
            file = tarfile.TarInfo(f'{root}/nested/restored.txt')
            file.size = len(content)
            archive.addfile(file, io.BytesIO(content))
    return stream.getvalue()


class Bundles(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='schooltest-bundle-')
        self.root = Path(self.work.name)
        self.backup = self.root / 'backup'
        self.backup.mkdir()
        (self.backup / 'database.dump').write_bytes(b'PGDMPsynthetic')
        (self.backup / 'files.tar.gz').write_bytes(archive_bytes())

    def tearDown(self): self.work.cleanup()

    def manifest(self):
        bundle.create_manifest(argparse.Namespace(directory=self.backup, commit='synthetic', image='synthetic-image'))

    def seed_volumes(self):
        for suffix in bundle.ROOTS.values():
            folder = self.root / 'volumes' / suffix
            folder.mkdir(parents=True)
            (folder / 'stale.txt').write_text('old')
            (folder / 'nested').mkdir()
            (folder / 'nested' / 'restored.txt').write_text('previous')
        return self.root / 'volumes'

    def test_checksums_and_complete_contents(self):
        self.manifest()
        bundle.verify(self.backup)
        with (self.backup / 'database.dump').open('ab') as target: target.write(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Checksum'): bundle.verify(self.backup)

    def test_bad_archive_rejected_without_touching_files(self):
        volumes = self.seed_volumes()
        for unsafe in ('../escape', '/absolute', 'data/../../escape'):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                member = tarfile.TarInfo(unsafe); member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
            with self.assertRaises(ValueError): bundle.restore_stream(io.BytesIO(stream.getvalue()), volumes)
        self.assertEqual((volumes / 'data/stale.txt').read_text(), 'old')

    def test_symlinks_rejected(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            member = tarfile.TarInfo('data/link'); member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
            archive.addfile(member)
        with self.assertRaisesRegex(ValueError, 'Links'): bundle.restore_stream(io.BytesIO(stream.getvalue()), self.root / 'volumes')

    def test_restore_replaces_instead_of_merging(self):
        volumes = self.seed_volumes()
        bundle.restore_stream(io.BytesIO(archive_bytes()), volumes)
        for suffix in bundle.ROOTS.values():
            self.assertFalse((volumes / suffix / 'stale.txt').exists())
            self.assertEqual((volumes / suffix / 'nested/restored.txt').read_text(), 'snapshot')
            self.assertFalse(list((volumes / suffix).glob('.schooltest-*')))

    def test_staging_failure_keeps_previous_contents(self):
        volumes = self.seed_volumes()
        replace = Path.replace
        failed = False
        def failure(source, target):
            nonlocal failed
            if '.schooltest-stage-' in str(source) and not failed:
                failed = True
                raise OSError('synthetic disk failure')
            return replace(source, target)
        with patch.object(Path, 'replace', failure):
            with self.assertRaises(OSError): bundle.restore_stream(io.BytesIO(archive_bytes()), volumes)
        for suffix in bundle.ROOTS.values():
            self.assertEqual((volumes / suffix / 'stale.txt').read_text(), 'old')
            self.assertEqual((volumes / suffix / 'nested/restored.txt').read_text(), 'previous')

    def test_legacy_and_uploads_only(self):
        (self.backup / 'files.tar.gz').write_bytes(archive_bytes(uploads=False))
        bundle.verify(self.backup)
        volumes = self.seed_volumes()
        bundle.restore_stream(io.BytesIO(archive_bytes(uploads=False)), volumes)
        self.assertEqual((volumes / 'app/static/uploads/stale.txt').read_text(), 'old')
        bundle.restore_stream(io.BytesIO(archive_bytes()), volumes, uploads_only=True)
        self.assertFalse((volumes / 'app/static/uploads/stale.txt').exists())
        self.assertEqual((volumes / 'data/nested/restored.txt').read_text(), 'snapshot')


class Scripts(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='schooltest-maintenance-')
        self.root = Path(self.work.name)
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts')
        self.bin = self.root / 'bin'; self.bin.mkdir()
        for name in ('docker', 'git', 'sleep'):
            path = self.bin / name; path.write_text(FAKE); path.chmod(0o755)
        (self.root / '.env').write_text('DUMMY=$(touch dangerous-eval-marker)\n')
        self.log = self.root / 'commands.jsonl'
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'MOCK_LOG': str(self.log), 'MOCK_ROOT': str(self.root), 'READY_TIMEOUT': '2', 'RESTORE_FORCE': '0'}
        self.backup = self.root / 'input-backup'; self.backup.mkdir()
        (self.backup / 'database.dump').write_bytes(b'PGDMPsynthetic')
        (self.backup / 'files.tar.gz').write_bytes(archive_bytes())
        bundle.create_manifest(argparse.Namespace(directory=self.backup, commit='synthetic', image='synthetic-image'))

    def tearDown(self): self.work.cleanup()

    def run_script(self, script, *args, failure=''):
        result = subprocess.run(['bash', str(self.root / 'scripts' / script), *map(str, args)],
                                cwd=self.root, env={**self.env, 'MOCK_FAIL': failure}, capture_output=True, text=True, timeout=15)
        self.assertFalse((self.root / 'dangerous-eval-marker').exists())
        return result

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_update_success_and_upload_migration(self):
        result = self.run_script('update.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertIn(['git', 'merge', '--ff-only', 'FETCH_HEAD'], commands)
        run = next(command for command in commands if command[:3] == ['docker', 'compose', 'run'])
        self.assertEqual(run[-1], 'restore-uploads')
        self.assertIn('HTTP readiness confirmed', result.stdout)
        self.assertEqual(len(list((self.root / 'backups').glob('*/manifest.json'))), 1)

    def test_failed_fetch_or_dirty_tree_does_not_stop_app(self):
        result = self.run_script('update.sh', failure='fetch origin main')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(['docker', 'compose', 'stop', 'app'], self.commands())
        self.env['MOCK_DIRTY'] = ' M README.md'
        result = self.run_script('update.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'backups').exists())

    def test_inspect_failure_does_not_continue_as_stopped(self):
        result = self.run_script('update.sh', failure='inspect --format {{.State.Running}}')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(['docker', 'compose', 'stop', 'app'], self.commands())
        self.assertFalse((self.root / 'backups').exists())

    def test_backup_failure_aborts_update_and_restarts_previous_container(self):
        result = self.run_script('update.sh', failure='pg_dump')
        self.assertNotEqual(result.returncode, 0)
        commands = self.commands()
        self.assertNotIn(['git', 'merge', '--ff-only', 'FETCH_HEAD'], commands)
        self.assertNotIn(['docker', 'compose', 'build', 'app'], commands)
        self.assertIn(['docker', 'start', 'synthetic-container'], commands)
        self.assertFalse(list((self.root / 'backups').glob('*/manifest.json')))

    def test_readiness_failure_leaves_deployed_app_stopped(self):
        result = self.run_script('update.sh', failure='urllib.request')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands()[-1], ['docker', 'compose', 'stop', 'app'])
        self.assertIn('left stopped', result.stderr)

    def test_restore_rejects_bad_checksum_before_docker_mutation(self):
        (self.backup / 'files.tar.gz').write_bytes(b'broken')
        result = self.run_script('restore.sh', self.backup, '-y')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(['docker', 'compose', 'stop', 'app'], self.commands())
        self.assertFalse(any('psql' in ' '.join(command) for command in self.commands()))

    def test_restore_safety_backup_failure_does_not_drop_database(self):
        result = self.run_script('restore.sh', self.backup, '-y', failure='pg_dump')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('psql' in ' '.join(command) for command in self.commands()))

    def test_restore_database_failure_does_not_restore_files_or_start_app(self):
        result = self.run_script('restore.sh', self.backup, '-y', failure='pg_restore --exit-on-error')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(command[:3] == ['docker', 'compose', 'run'] for command in self.commands()))
        self.assertEqual(self.commands()[-1], ['docker', 'compose', 'stop', 'app'])

    def test_restore_success(self):
        result = self.run_script('restore.sh', self.backup, '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        stop = commands.index(['docker', 'compose', 'stop', 'app'])
        sql = next(i for i, command in enumerate(commands) if 'psql' in ' '.join(command))
        self.assertLess(stop, sql)
        run = next(command for command in commands if command[:3] == ['docker', 'compose', 'run'])
        self.assertEqual(run[-1], 'restore-files')
        self.assertIn('HTTP readiness confirmed', result.stdout)


if __name__ == '__main__': unittest.main(verbosity=2)
