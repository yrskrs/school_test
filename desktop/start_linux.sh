#!/bin/bash
# Запуск SchoolTest Server Manager (Linux)

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "$LAUNCHER_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Використовуємо Python для запуску повністю незалежного процесу (daemon)
python3 -c "import sys, subprocess; subprocess.Popen([sys.executable, 'desktop/run_gui.py'], start_new_session=True)"

exit 0
