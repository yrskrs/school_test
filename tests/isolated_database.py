"""Only disposable SQLite or the explicitly named Docker audit DB is allowed."""
import os
from pathlib import Path

from sqlalchemy.engine import make_url


AUDIT_URL = 'postgresql://audit:audit-synthetic@school-audit-postgres:5432/schooltest5'
_SQLITE_DIRECTORIES = set()


def configure(work_directory):
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    postgres = os.environ.get('SCHOOLTEST_TEST_POSTGRES') == '1'
    os.environ['DATABASE_URL'] = AUDIT_URL if postgres else f'sqlite:///{work_directory}/checks.db'
    if not postgres:
        _SQLITE_DIRECTORIES.add(Path(work_directory).resolve())


def validate_engine(engine, work_directory):
    if os.environ.get('SCHOOLTEST_TEST_POSTGRES') == '1':
        valid = engine.url == make_url(AUDIT_URL)
    else:
        # unittest discovery imports both fixture modules before running tests.
        # Their cached application engine can belong to either approved temp dir.
        valid = engine.dialect.name == 'sqlite' and any(
            Path(engine.url.database).resolve().is_relative_to(directory)
            for directory in _SQLITE_DIRECTORIES
        )
    if not valid:
        raise RuntimeError('Tests require their own disposable database in a separate process')
