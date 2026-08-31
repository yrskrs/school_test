import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# Import models to ensure they are registered in Base.metadata
from app.database import Base
from app import models

def migrate():
    sqlite_url = f"sqlite:///{BASE_DIR / 'data' / 'school_testing.db'}"
    
    # Read Postgres URL from env or use default for docker-compose
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    postgres_url = os.getenv("DATABASE_URL")
    
    if not postgres_url or postgres_url.startswith("sqlite"):
        print("Error: DATABASE_URL in .env is not a PostgreSQL URL.")
        print(f"Current DATABASE_URL: {postgres_url}")
        sys.exit(1)
        
    print(f"Migrating from SQLite: {sqlite_url}")
    print(f"To PostgreSQL: {postgres_url}")
    
    sqlite_engine = create_engine(sqlite_url)
    postgres_engine = create_engine(postgres_url)
    
    # Check if SQLite DB exists and has tables
    try:
        sqlite_conn = sqlite_engine.connect()
        sqlite_conn.close()
    except Exception as e:
        print(f"Cannot connect to SQLite: {e}")
        sys.exit(1)
        
    # Create tables in Postgres
    print("Creating tables in PostgreSQL...")
    Base.metadata.create_all(postgres_engine)
    
    # Migrate data table by table
    # We must respect foreign key constraints, so we disable them temporarily during migration
    # or insert in the correct order. SQLAlchemy metadata.sorted_tables gives the correct order!
    
    sqlite_Session = sessionmaker(bind=sqlite_engine)
    postgres_Session = sessionmaker(bind=postgres_engine)
    
    sqlite_session = sqlite_Session()
    postgres_session = postgres_Session()
    
    try:
        # Disable triggers for foreign keys temporarily on postgres
        postgres_session.execute(org_sqlalchemy_text("SET session_replication_role = 'replica';"))
        
        for table in Base.metadata.sorted_tables:
            print(f"Migrating table: {table.name}")
            # Fetch all rows from sqlite
            rows = sqlite_session.execute(table.select()).fetchall()
            if not rows:
                print(f"  - No data in {table.name}, skipping.")
                continue
                
            # Clear existing rows in postgres table (optional, but good if we run it multiple times on an empty db)
            postgres_session.execute(table.delete())
            
            # Insert rows to postgres
            # rows are list of rows, we convert them to list of dicts
            dicts = [dict(row._mapping) for row in rows]
            postgres_session.execute(table.insert(), dicts)
            print(f"  - Migrated {len(dicts)} rows.")
            
        # Re-enable triggers
        postgres_session.execute(org_sqlalchemy_text("SET session_replication_role = 'origin';"))
        
        postgres_session.commit()
        print("Migration completed successfully!")
        
    except Exception as e:
        postgres_session.rollback()
        print(f"Error during migration: {e}")
        # Make sure to reset replication role even on error
        try:
            postgres_session.execute(org_sqlalchemy_text("SET session_replication_role = 'origin';"))
            postgres_session.commit()
        except:
            pass
        sys.exit(1)
    finally:
        sqlite_session.close()
        postgres_session.close()

if __name__ == "__main__":
    from sqlalchemy import text as org_sqlalchemy_text
    migrate()
