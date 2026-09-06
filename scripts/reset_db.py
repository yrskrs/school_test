#!/usr/bin/env python3
"""
Скрипт для повного очищення бази даних від користувачів, тестів, сесій,
результатів та всіх створених ними завантажень і архівів.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# Додаємо кореневу директорію проєкту до sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def clean_directory(dir_path: Path, preserve_gitkeep: bool = True):
    """Очищує вміст директорії, опціонально зберігаючи .gitkeep."""
    if not dir_path.exists():
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            if preserve_gitkeep:
                (dir_path / ".gitkeep").touch()
        except Exception:
            pass
        return

    for item in dir_path.iterdir():
        if preserve_gitkeep and item.name == ".gitkeep":
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except Exception as e:
            print(f"   [i] Пропуск видалення {item.name}: {e}")

    if preserve_gitkeep:
        try:
            (dir_path / ".gitkeep").touch()
        except Exception:
            pass


def reset_sqlalchemy_db():
    """Очищує всі таблиці в активній базі даних через SQLAlchemy."""
    print("-> Очищення активної бази даних (SQLAlchemy)...")
    try:
        from sqlalchemy import text
        from app.database import engine, create_tables
        create_tables()
        with engine.connect() as conn:
            trans = conn.begin()
            dialect_name = engine.dialect.name
            try:
                if dialect_name == "sqlite":
                    conn.execute(text("PRAGMA foreign_keys = OFF;"))
                    tables = [
                        "student_answers",
                        "student_attempts",
                        "event_logs",
                        "test_sessions",
                        "answer_options",
                        "questions",
                        "teacher_logs",
                        "tests",
                        "teachers",
                    ]
                    for tbl in tables:
                        try:
                            conn.execute(text(f"DELETE FROM {tbl};"))
                            conn.execute(text(f"DELETE FROM sqlite_sequence WHERE name='{tbl}';"))
                        except Exception:
                            pass
                    conn.execute(text("PRAGMA foreign_keys = ON;"))
                elif dialect_name == "postgresql":
                    conn.execute(text("""
                        TRUNCATE TABLE 
                            student_answers,
                            student_attempts,
                            event_logs,
                            test_sessions,
                            answer_options,
                            questions,
                            teacher_logs,
                            tests,
                            teachers
                        RESTART IDENTITY CASCADE;
                    """))
                trans.commit()
                print(f"   [OK] Таблиці успішно очищено (діалект: {dialect_name}).")
            except Exception as exc:
                trans.rollback()
                print(f"   [!] Помилка очищення таблиць через SQLAlchemy: {exc}")
    except Exception as exc:
        print(f"   [i] Пропуск прямого з'єднання SQLAlchemy ({exc}). Перевірка SQLite та Docker напряму.")


def reset_sqlite_file():
    """Очищує локальну SQLite базу даних data/school_testing.db, якщо вона існує."""
    sqlite_db_path = BASE_DIR / "data" / "school_testing.db"
    if sqlite_db_path.exists():
        print("-> Очищення локальної SQLite БД...")
        try:
            import sqlite3
            con = sqlite3.connect(sqlite_db_path)
            cur = con.cursor()
            cur.execute("PRAGMA foreign_keys = OFF;")
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for tbl in tables:
                if tbl != "sqlite_sequence":
                    cur.execute(f"DELETE FROM {tbl};")
            try:
                cur.execute("DELETE FROM sqlite_sequence;")
            except Exception:
                pass
            cur.execute("PRAGMA foreign_keys = ON;")
            con.commit()
            cur.execute("VACUUM;")
            con.close()
            print("   [OK] Локальний файл data/school_testing.db повністю очищено від записів.")
        except Exception as e:
            print(f"   [!] Не вдалося очистити data/school_testing.db: {e}")


def reset_docker_postgres():
    """Очищує базу даних у запущеному Docker-контейнері PostgreSQL, якщо він активний."""
    try:
        check_res = subprocess.run(
            ["docker", "ps", "--filter", "name=postgres", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        containers = [c.strip() for c in check_res.stdout.strip().split("\n") if c.strip()]
        for pg_container in containers:
            if "schooltest" in pg_container or "postgres" in pg_container:
                print(f"-> Очищення таблиць у Docker PostgreSQL контейнері ({pg_container})...")
                truncate_sql = "TRUNCATE TABLE student_answers, student_attempts, event_logs, test_sessions, answer_options, questions, teacher_logs, tests, teachers RESTART IDENTITY CASCADE;"
                res = subprocess.run(
                    ["docker", "exec", pg_container, "psql", "-U", "appuser", "-d", "schooltest", "-c", truncate_sql],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if res.returncode == 0:
                    print(f"   [OK] Таблиці у Docker PostgreSQL ({pg_container}) очищено.")
                else:
                    # Спроба з користувачем postgres за замовчуванням
                    res2 = subprocess.run(
                        ["docker", "exec", pg_container, "psql", "-U", "postgres", "-d", "schooltest", "-c", truncate_sql],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if res2.returncode == 0:
                        print(f"   [OK] Таблиці у Docker PostgreSQL ({pg_container}) очищено.")
    except Exception as e:
        print(f"   [!] Повідомлення Docker Postgres: {e}")


def clean_user_files():
    """Видаляє всі створені користувачами файли, зображення та архіви."""
    print("-> Видалення медіа-файлів та архівів користувачів...")
    paths_to_clean = [
        BASE_DIR / "app" / "static" / "tests",
        BASE_DIR / "app" / "static" / "uploads",
        BASE_DIR / "data" / "archives",
        BASE_DIR / "data" / "results",
        BASE_DIR / "data" / "temp_archive",
        BASE_DIR / "scratch",
    ]

    for p in paths_to_clean:
        clean_directory(p, preserve_gitkeep=True)

    # Очищення файлів усередині Docker-контейнера, якщо він запущений
    try:
        subprocess.run(
            ["docker", "compose", "exec", "-T", "app", "sh", "-c",
             "find /app/app/static/tests -mindepth 1 -delete 2>/dev/null || true; "
             "find /app/data/archives -mindepth 1 -delete 2>/dev/null || true; "
             "find /app/data/results -mindepth 1 -delete 2>/dev/null || true; "
             "find /app/data/temp_archive -mindepth 1 -delete 2>/dev/null || true"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass

    # Очищення додаткових файлів
    last_archive = BASE_DIR / "data" / "last_archive_time.txt"
    if last_archive.exists():
        last_archive.unlink()

    for log_name in ["gui.log"]:
        log_file = BASE_DIR / "logs" / log_name
        if log_file.exists():
            try:
                log_file.write_text("", encoding="utf-8")
            except Exception:
                pass

    # Очищення старих бекапів з директорії backups/
    backups_dir = BASE_DIR / "backups"
    if backups_dir.exists():
        for b_item in backups_dir.iterdir():
            if b_item.is_dir():
                shutil.rmtree(b_item, ignore_errors=True)

    print("   [OK] Усі файли користувачів, тестові папки, архіви та логи успішно очищено.")


def main():
    print("==================================================")
    print("   Скидання даних ШколярТест (SchoolTest)       ")
    print("==================================================")
    reset_sqlalchemy_db()
    reset_sqlite_file()
    reset_docker_postgres()
    clean_user_files()
    print("==================================================")
    print(" УСПІШНО: Система готова до першого запуску (/setup)!")
    print("==================================================")


if __name__ == "__main__":
    main()
