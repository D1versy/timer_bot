import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Файл SQLite в корне проекта (рядом с bot.py)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_project_root, "app.db")
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def ensure_db_exists():
    """Создаёт файл БД и таблицы, если их ещё нет."""
    from . import models  # noqa: F401 — регистрируем таблицы
    Base.metadata.create_all(bind=engine)