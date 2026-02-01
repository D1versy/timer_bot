"""Скрипт загрузки боссов в БД. Запуск: python -m app.seed"""
import sys
import os

# Запуск из корня проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine, SessionLocal, Base
from app.models import Boss, ServerState
from app.seed_data import BOSSES


def run(reset: bool = False):
    if reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Один ряд в server_state
        if db.query(ServerState).first() is None:
            db.add(ServerState(id=1, server_restart_at=None))
            db.commit()

        existing = {b.name for b in db.query(Boss).all()}
        added = 0
        for row in BOSSES:
            if row["name"] in existing:
                continue
            db.add(Boss(
                name=row["name"],
                spawn_chance_percent=row["chance"],
                first_spawn_minutes=row["first_spawn_minutes"],
                respawn_minutes=row["respawn_minutes"],
            ))
            added += 1
        db.commit()
        print(f"Готово. Добавлено боссов: {added}, всего в БД: {db.query(Boss).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    reset = "--reset" in sys.argv or "-r" in sys.argv
    run(reset=reset)
