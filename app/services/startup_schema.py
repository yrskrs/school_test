"""Idempotent additive upgrades for the existing SQLite/PostgreSQL schema."""
from sqlalchemy import inspect, text


def ensure_legacy_columns(engine):
    upgrades = {
        "teachers": {"subject": "VARCHAR(100)", "classes": "VARCHAR(255)"},
        "tests": {"use_fuzzy_matching": "BOOLEAN DEFAULT FALSE"},
    }
    for table, definitions in upgrades.items():
        present = {column["name"] for column in inspect(engine).get_columns(table)}
        for column, declaration in definitions.items():
            if column not in present:
                # Each change has its own transaction; errors roll back and
                # surface instead of leaving PostgreSQL in an aborted transaction.
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"))
