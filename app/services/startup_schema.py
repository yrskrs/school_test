"""Idempotent additive upgrades for the existing SQLite/PostgreSQL schema."""
from sqlalchemy import inspect, text


def ensure_legacy_columns(engine):
    upgrades = {
        "teachers": {"subject": "VARCHAR(100)", "classes": "VARCHAR(255)"},
        "tests": {"use_fuzzy_matching": "BOOLEAN DEFAULT FALSE"},
        "test_sessions": {"roster_class_id": "INTEGER REFERENCES roster_classes(id)",
            "roster_subject_id": "INTEGER REFERENCES roster_subjects(id)",
            "lesson_date": "VARCHAR(10)", "lesson_number": "INTEGER"},
        "student_attempts": {"roster_student_id": "INTEGER REFERENCES roster_students(id)", "updated_at": "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "TIMESTAMP"},
    }
    for table, definitions in upgrades.items():
        if table in ('test_sessions', 'student_attempts') and not inspect(engine).has_table(table):
            continue
        present = {column["name"] for column in inspect(engine).get_columns(table)}
        for column, declaration in definitions.items():
            if column not in present:
                # Each change has its own transaction; errors roll back and
                # surface instead of leaving PostgreSQL in an aborted transaction.
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"))
