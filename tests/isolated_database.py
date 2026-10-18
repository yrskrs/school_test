"""Only disposable SQLite or the explicitly named Docker audit DB is allowed."""
import os
from pathlib import Path

from sqlalchemy.engine import make_url


AUDIT_URL = 'postgresql://audit:audit-synthetic@school-audit-postgres:5432/schooltest5'


def configure(work_directory):
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    postgres = os.environ.get('SCHOOLTEST_TEST_POSTGRES') == '1'
    os.environ['DATABASE_URL'] = AUDIT_URL if postgres else f'sqlite:///{work_directory}/checks.db'


def validate_engine(engine, work_directory):
    if os.environ.get('SCHOOLTEST_TEST_POSTGRES') == '1':
        valid = engine.url == make_url(AUDIT_URL)
    else:
        valid = engine.dialect.name == 'sqlite' and Path(engine.url.database).resolve().is_relative_to(Path(work_directory).resolve())
    if not valid:
        raise RuntimeError('Tests require their own disposable database in a separate process')
