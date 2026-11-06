import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

is_sqlite = settings.DATABASE_URL.startswith("sqlite")

connect_args = {}
if is_sqlite:
    connect_args = {
        "check_same_thread": False,
        "timeout": 60,
    }

# A classroom can issue answer, progress and status requests concurrently.
# Keep a finite pool below PostgreSQL max_connections (100).
pool_options = {} if is_sqlite else {
    "pool_size": int(os.environ.get("SCHOOLTEST_DB_POOL_SIZE", "20")),
    "max_overflow": int(os.environ.get("SCHOOLTEST_DB_MAX_OVERFLOW", "20")),
    "pool_timeout": 10,
    "pool_pre_ping": True,
}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    **pool_options,
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        # SQLite's built-in lower() does not fold Ukrainian letters.
        dbapi_connection.create_function("unicode_casefold", 1, lambda value: str(value).casefold() if value is not None else None, deterministic=True)
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
