#!/usr/bin/env python3
import sys
import os

# Додаємо кореневу директорію до PYTHONPATH, щоб імпорти працювали правильно
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main import main

if __name__ == "__main__":
    main()
