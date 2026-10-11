"""Copy a stopped SQLite installation into an empty PostgreSQL database."""
import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))


def migrate(sqlite_path=None, postgres_url=None):
    source_path = Path(sqlite_path or BASE_DIR / 'data' / 'school_testing.db').resolve()
    if not source_path.is_file():
        raise ValueError('SQLite source file does not exist; no database was created.')
    load_dotenv(BASE_DIR / '.env')
    destination_url = make_url(postgres_url or os.getenv('DATABASE_URL', ''))
    if destination_url.get_backend_name() != 'postgresql':
        raise ValueError('Destination must be a PostgreSQL URL.')

    from app.database import Base
    from app import models  # Register the application tables.

    source = create_engine(f'sqlite:///{source_path.as_uri()}?mode=ro&uri=true')
    destination = create_engine(destination_url, hide_parameters=True)
    counts = {}
    try:
        with source.connect() as reader:
            reader.exec_driver_sql('BEGIN')
            source_tables = set(inspect(reader).get_table_names())
            if 'teachers' not in source_tables:
                raise ValueError('SQLite source is not a SchoolTest database.')
            tables = list(Base.metadata.sorted_tables)
            if source_tables - set(Base.metadata.tables):
                raise ValueError('Source contains unsupported tables; use a matching application version.')
            for table in tables:
                if table.name not in source_tables:
                    continue
                columns = {column['name'] for column in inspect(reader).get_columns(table.name)}
                if set(table.c.keys()) != columns:
                    raise ValueError(f'Source schema differs for {table.name}; update the local SQLite installation first.')
            # DDL, empty-target check and all copied rows share one transaction.
            with destination.begin() as writer:
                Base.metadata.create_all(writer)
                quoted = ', '.join(writer.dialect.identifier_preparer.format_table(table) for table in tables)
                writer.execute(text(f'LOCK TABLE {quoted} IN ACCESS EXCLUSIVE MODE'))
                if any(writer.execute(select(table).limit(1)).first() is not None for table in tables):
                    raise ValueError('PostgreSQL destination is not empty; existing records were preserved.')
                for table in tables:
                    if table.name not in source_tables:
                        continue
                    count = 0
                    rows = reader.execute(select(table)).mappings()
                    for batch in rows.partitions(500):
                        writer.execute(table.insert(), [dict(row) for row in batch])
                        count += len(batch)
                    counts[table.name] = count
                # Explicit IDs do not advance PostgreSQL sequences. Transactional
                # RESTART avoids an ID collision on the next ordinary insert.
                for table in tables:
                    primary_key = list(table.primary_key.columns)
                    if len(primary_key) != 1 or primary_key[0].name != 'id':
                        continue
                    sequence = writer.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                              {'table': table.name, 'column': 'id'}).scalar_one()
                    if sequence:
                        maximum = writer.execute(select(primary_key[0]).order_by(primary_key[0].desc()).limit(1)).scalar_one_or_none()
                        # The name comes from PostgreSQL; quote every component.
                        quoted_sequence = '.'.join(writer.dialect.identifier_preparer.quote_identifier(part)
                                                   for part in sequence.split('.'))
                        writer.execute(text(f'ALTER SEQUENCE {quoted_sequence} RESTART WITH {max(1, (maximum or 0) + 1)}'))
        return counts
    finally:
        source.dispose()
        destination.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', type=Path, help='Path to the stopped SQLite source (opened read-only).')
    args = parser.parse_args()
    try:
        counts = migrate(args.sqlite)
    except ValueError as error:
        print(f'Migration refused: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        # Driver exceptions can contain credentials, SQL values and pupil data.
        print(f'Migration failed ({type(error).__name__}); copied rows were rolled back. Check connectivity and source schema.', file=sys.stderr)
        return 1
    for table, count in counts.items():
        print(f'{table}: {count} rows')
    print('Migration completed. PostgreSQL ID sequences updated; SQLite source preserved.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
