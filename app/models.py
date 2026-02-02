from sqlalchemy import Integer, String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from .db import Base


class Boss(Base):
    __tablename__ = "bosses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    spawn_chance_percent: Mapped[int] = mapped_column(Integer)  # шанс появления %
    first_spawn_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # первое появление после рестарта (мин)
    respawn_minutes: Mapped[int] = mapped_column(Integer)  # интервал респауна (мин)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_kill_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KillLog(Base):
    __tablename__ = "kill_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    boss_id: Mapped[int] = mapped_column(Integer, index=True)
    killed_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(String, nullable=True)


class ServerState(Base):
    __tablename__ = "server_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_restart_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notification_intervals: Mapped[str | None] = mapped_column(String, nullable=True, default="15,5,1")  # минуты через запятую


class Subscriber(Base):
    """Подписчики на уведомления о боссах."""
    __tablename__ = "subscribers"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)