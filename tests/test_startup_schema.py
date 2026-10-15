"""Startup upgrade regression checks using isolated in-memory databases."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from sqlalchemy.exc import NoSuchTableError
from app.services.startup_schema import ensure_legacy_columns


class StartupSchema(unittest.TestCase):
    def test_outside_identity_upgrade_keeps_existing_attempt_and_mark(self):
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE teachers (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE tests (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE student_attempts (id INTEGER PRIMARY KEY, student_name VARCHAR(200), score FLOAT)'))
            connection.execute(text("INSERT INTO student_attempts VALUES (7, 'Synthetic pupil', 9)"))
        ensure_legacy_columns(engine)
        ensure_legacy_columns(engine)
        with engine.connect() as connection:
            self.assertEqual(tuple(connection.execute(text('SELECT id, student_name, score, outside_student_id FROM student_attempts')).one()),
                             (7, 'Synthetic pupil', 9, None))
        engine.dispose()

    def test_partial_legacy_schema_and_repeated_startup_preserve_data(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE teachers (id INTEGER PRIMARY KEY, subject VARCHAR(100))"))
            connection.execute(text("CREATE TABLE tests (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO teachers VALUES (1, 'existing-subject')"))
            connection.execute(text("INSERT INTO tests VALUES (1)"))
        ensure_legacy_columns(engine)
        ensure_legacy_columns(engine)
        with engine.connect() as connection:
            row = connection.execute(text("SELECT subject, classes FROM teachers")).one()
            self.assertEqual(tuple(row), ("existing-subject", None))
            self.assertEqual(connection.execute(text("SELECT use_fuzzy_matching FROM tests")).scalar_one(), 0)
        engine.dispose()

    def test_unexpected_schema_failure_is_not_hidden(self):
        engine = create_engine("sqlite:///:memory:")
        with self.assertRaises(NoSuchTableError):
            ensure_legacy_columns(engine)
        engine.dispose()

    def test_progress_upgrade_preserves_existing_paused_attempt(self):
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE teachers (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE tests (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE student_attempts (id INTEGER PRIMARY KEY, status VARCHAR(20))'))
            connection.execute(text("INSERT INTO student_attempts VALUES (5, 'paused')"))
        ensure_legacy_columns(engine)
        with engine.begin() as connection:
            self.assertEqual(tuple(connection.execute(text('SELECT id, status, progress_state FROM student_attempts')).one()),
                             (5, 'paused', None))
            connection.execute(text('UPDATE student_attempts SET progress_state = :state WHERE id=5'), {'state': '{"time_left":117}'})
        ensure_legacy_columns(engine)
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT progress_state FROM student_attempts')).scalar_one(), '{"time_left":117}')
        engine.dispose()

    def test_legacy_retake_policy_and_saved_new_settings_survive_upgrade(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE teachers (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE tests (id INTEGER PRIMARY KEY, allow_retake BOOLEAN)"))
            connection.execute(text("INSERT INTO tests VALUES (1, FALSE), (2, TRUE)"))
        ensure_legacy_columns(engine)
        with engine.begin() as connection:
            rows = connection.execute(text("SELECT allow_retake, max_attempts, allow_outside_class FROM tests ORDER BY id")).all()
            self.assertEqual([tuple(row) for row in rows], [(0, None, 0), (1, None, 0)])
            connection.execute(text("UPDATE tests SET max_attempts = 3, allow_outside_class = TRUE WHERE id = 2"))
        ensure_legacy_columns(engine)
        with engine.connect() as connection:
            self.assertEqual(tuple(connection.execute(text("SELECT max_attempts, allow_outside_class FROM tests WHERE id = 2")).one()), (3, 1))
        engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
