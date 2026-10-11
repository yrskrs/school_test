"""Migration preflight and error output; no production connections."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sqlite_migration', Path(__file__).resolve().parents[1] / 'scripts' / 'migrate_sqlite_to_postgres.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationPreflightTests(unittest.TestCase):
    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.db'
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                migration.migrate(path, 'postgresql://synthetic:secret@invalid/checks')
            self.assertFalse(path.exists())

    def test_non_postgresql_target_is_rejected_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            source.touch()
            target = Path(directory) / 'target.db'
            with self.assertRaisesRegex(ValueError, 'PostgreSQL URL'):
                migration.migrate(source, f'sqlite:///{target}')
            self.assertFalse(target.exists())

    def test_driver_error_does_not_print_credentials_or_row_values(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['migrate']), patch.object(migration, 'migrate', side_effect=RuntimeError('synthetic-password synthetic-pupil')):
            with contextlib.redirect_stderr(output):
                self.assertEqual(migration.main(), 1)
        self.assertIn('RuntimeError', output.getvalue())
        self.assertNotIn('synthetic-password', output.getvalue())
        self.assertNotIn('synthetic-pupil', output.getvalue())
