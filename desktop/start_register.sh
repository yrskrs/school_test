#!/bin/bash
# Запуск SchoolTest Register Manager (Linux)

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "$LAUNCHER_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

if [ -f ".env" ]; then
    # Завантажуємо змінні з .env
    set -a
    source .env
    set +a
    # Замінюємо хост postgres на localhost для локального підключення
    export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
fi

# Запуск вікна реєстрації
python3 desktop/run_register.py "$@"
