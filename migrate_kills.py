#!/usr/bin/env python3
"""Перенос last_kill_at из app_old.db в app.db. Сопоставление по имени босса."""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
OLD_DB = PROJECT_ROOT / "app_old.db"
NEW_DB = PROJECT_ROOT / "app.db"


def main():
    if not OLD_DB.exists():
        raise SystemExit(f"❌ Файл не найден: {OLD_DB}")
    if not NEW_DB.exists():
        raise SystemExit(f"❌ Файл не найден: {NEW_DB}")

    conn_old = sqlite3.connect(OLD_DB)
    conn_old.row_factory = sqlite3.Row
    cur_old = conn_old.execute(
        "SELECT id, name, last_kill_at FROM bosses WHERE last_kill_at IS NOT NULL"
    )
    old_bosses = {row["name"].strip(): row["last_kill_at"] for row in cur_old}
    conn_old.close()

    if not old_bosses:
        print("В старой БД нет записей с last_kill_at. Ничего не переносим.")
        return

    conn_new = sqlite3.connect(NEW_DB)
    cur_new = conn_new.execute("SELECT id, name FROM bosses")
    new_bosses = list(cur_new.fetchall())
    updated = 0
    for boss_id, name in new_bosses:
        name_clean = name.strip()
        if name_clean in old_bosses:
            last_kill_at = old_bosses[name_clean]
            conn_new.execute(
                "UPDATE bosses SET last_kill_at = ? WHERE id = ?",
                (last_kill_at, boss_id),
            )
            updated += 1
            print(f"  [{boss_id}] {name}: last_kill_at = {last_kill_at}")

    conn_new.commit()
    conn_new.close()

    print(f"\n✅ Перенесено время убийства для {updated} боссов (из {len(new_bosses)} в новой БД).")


if __name__ == "__main__":
    main()
