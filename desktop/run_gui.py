#!/usr/bin/env python3
import sys
import os

# Додаємо кореневу директорію до PYTHONPATH, щоб імпорти працювали правильно
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from gui.main import main

if __name__ == "__main__":
    main()
