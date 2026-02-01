#!/usr/bin/env python3
"""Создаёт файл SQLite БД (app.db) и все таблицы в корне проекта."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import ensure_db_exists, DB_PATH

if __name__ == "__main__":
    ensure_db_exists()
    print(f"БД создана: {DB_PATH}")
    print("Чтобы загрузить 54 босса, выполните: python -m app.seed --reset")
