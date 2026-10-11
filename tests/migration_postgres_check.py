"""Migration checks against a disposable database on the runtime-check PG."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
assert url.host == 'postgres' and url.database == 'schooltest_runtime_check' and url.username == 'runtime_check'
admin = create_engine(url, isolation_level='AUTOCOMMIT')
with admin.connect() as connection:
    connection.execute(text('CREATE DATABASE schooltest_migration_check'))
admin.dispose()
target_url = url.set(database='schooltest_migration_check')
target = create_engine(target_url)

from app.database import Base
from app import models

spec = importlib.util.spec_from_file_location('sqlite_migration', '/app/scripts/migrate_sqlite_to_postgres.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'source.db'
    source = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(source)
    with source.begin() as connection:
        connection.execute(models.Teacher.__table__.insert(), {'id': 41, 'username': 'migration', 'full_name': 'Synthetic Teacher', 'hashed_password': 'synthetic'})
        connection.execute(models.Test.__table__.insert(), {'id': 73, 'teacher_id': 41, 'title': 'Synthetic Test'})
    source.dispose()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    counts = migration.migrate(path, str(target_url.render_as_string(hide_password=False)))
    assert counts['teachers'] == 1 and counts['tests'] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with target.begin() as connection:
        assert connection.execute(select(models.Test.teacher_id)).scalar_one() == 41
        teacher_id = connection.execute(models.Teacher.__table__.insert().returning(models.Teacher.id), {'username': 'next', 'full_name': 'Next Synthetic', 'hashed_password': 'synthetic'}).scalar_one()
        assert teacher_id == 42
        test_id = connection.execute(models.Test.__table__.insert().returning(models.Test.id), {'teacher_id': teacher_id, 'title': 'Next Test'}).scalar_one()
        assert test_id == 74
    try:
        migration.migrate(path, str(target_url.render_as_string(hide_password=False)))
    except ValueError as error:
        assert 'not empty' in str(error)
    else:
        raise AssertionError('Migration accepted an occupied database')
    with target.connect() as connection:
        assert len(connection.execute(select(models.Teacher.id)).all()) == 2
    print('Migration: source preserved, rows/FKs copied, next IDs correct, occupied target refused')
    Base.metadata.drop_all(target)
    source = create_engine(f'sqlite:///{path}')
    with source.begin() as connection:
        connection.execute(models.Test.__table__.update().values(teacher_id=999))
    source.dispose()
    try:
        migration.migrate(path, str(target_url.render_as_string(hide_password=False)))
    except Exception as error:
        assert type(error).__name__ == 'IntegrityError'
    else:
        raise AssertionError('Invalid FK accepted')
    with target.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.teachers')")).scalar_one() is None
    print('Migration: invalid FK rolls back copied rows and newly created tables')

target.dispose()
