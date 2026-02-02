"""Логика расчёта следующего респауна. Время везде по Simferopol (UTC+3)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Simferopol")  # UTC+3


def now_moscow() -> datetime:
    return datetime.now(MOSCOW)


def parse_moscow_naive(dt: datetime) -> datetime:
    """Интерпретировать naive datetime как Simferopol время."""
    return dt.replace(tzinfo=MOSCOW)


def next_spawn_at(
    last_kill_at: datetime | None,
    server_restart_at: datetime | None,
    first_spawn_minutes: int | None,
    respawn_minutes: int,
) -> datetime | None:
    """
    Рассчитать время следующего появления босса (одна точка во времени).
    Все datetime должны быть timezone-aware (Simferopol).
    
    Логика:
    - Если есть last_kill_at → считаем: last_kill_at + respawn_minutes
    - Если last_kill_at is None → считаем от рестарта по first_spawn_minutes
    
    При /restart все last_kill_at сбрасываются в NULL.
    При /kill записывается last_kill_at.
    
    Время может быть в прошлом — это нормально (босс уже появился, ждём /kill).
    """
    # Защита от бесконечного цикла при respawn_minutes <= 0
    if respawn_minutes <= 0:
        return None

    # Если есть last_kill_at — считаем от него по resp (без прокрутки циклов)
    if last_kill_at is not None:
        return last_kill_at + timedelta(minutes=respawn_minutes)

    # Нет килла — считаем от рестарта
    if server_restart_at is None:
        return None

    if first_spawn_minutes is not None:
        # Есть first_spawn_minutes → первое появление через это время после рестарта
        return server_restart_at + timedelta(minutes=first_spawn_minutes)
    else:
        # Нет first_spawn_minutes → появляется сразу после рестарта
        return server_restart_at
