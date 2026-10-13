"""Startup upgrade regression checks using isolated in-memory databases."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from sqlalchemy.exc import NoSuchTableError
from app.services.startup_schema import ensure_legacy_columns


class StartupSchema(unittest.TestCase):
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
