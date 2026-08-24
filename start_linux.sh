#!/bin/bash
# Запуск SchoolTest Server Manager (Linux)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Використовуємо Python для запуску повністю незалежного процесу (daemon)
python3 -c "import sys, subprocess; subprocess.Popen([sys.executable, 'run_gui.py'], start_new_session=True)"

exit 0
