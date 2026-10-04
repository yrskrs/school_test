"""Validate PostgreSQL drivers and application imports without accessing a DB."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = "postgresql://runtime_check:synthetic@127.0.0.1/runtime_check"
os.environ["SECRET_KEY"] = "runtime-import-check-only"

from sqlalchemy import create_engine


def main():
    # Bare PostgreSQL URLs select psycopg2 on SQLAlchemy 2.0 and psycopg on 2.1.
    # Keep both explicit URL formats working for existing installations.
    for dialect in ("postgresql", "postgresql+psycopg", "postgresql+psycopg2"):
        engine = create_engine(f"{dialect}://runtime_check:synthetic@127.0.0.1/runtime_check")
        engine.dispose()
        print(f"Runtime import OK: {dialect}")
    from app.main import app
    from app.templating import templates
    for name in templates.env.list_templates():
        templates.env.get_template(name)
    print(f"Application import and templates OK: {app.version}")


if __name__ == "__main__":
    main()
