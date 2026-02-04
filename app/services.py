"""Логика расчёта следующего респауна. Время везде по Simferopol (UTC+3)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math

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
    now: datetime | None = None,
) -> datetime | None:
    """
    Рассчитать время следующего появления босса (одна точка во времени).
    Все datetime должны быть timezone-aware (Simferopol).
    
    Логика:
    1. Если есть last_kill_at → считаем: last_kill_at + respawn_minutes
       (ручной /kill всегда переопределяет расписание)
    
    2. Если last_kill_at is None (после /restart):
       - Первое появление: server_restart_at + first_spawn_minutes
         (или сразу server_restart_at, если first_spawn_minutes is None)
       - Если first_spawn_at уже в прошлом (now > first_spawn_at):
         "догоняем" расписание, вычисляя ближайший будущий респ по формуле:
         next = first_spawn_at + k * respawn_minutes, где k = ceil((now - first_spawn_at) / respawn_minutes)
    
    При /restart все last_kill_at сбрасываются в NULL.
    При /kill записывается last_kill_at.
    
    Параметр now нужен для "догонки" — если не передан, используется текущее время.
    """
    # Защита от бесконечного цикла при respawn_minutes <= 0
    if respawn_minutes <= 0:
        return None

    # Если есть last_kill_at — считаем от него по resp (один цикл, без догонки)
    if last_kill_at is not None:
        return last_kill_at + timedelta(minutes=respawn_minutes)

    # Нет килла — считаем от рестарта с догонкой до текущего времени
    if server_restart_at is None:
        return None

    # Определяем время первого появления после рестарта
    if first_spawn_minutes is not None:
        first_spawn_at = server_restart_at + timedelta(minutes=first_spawn_minutes)
    else:
        # Нет first_spawn_minutes → босс появляется сразу при рестарте (first = 0)
        first_spawn_at = server_restart_at

    # Если now не передан, используем текущее время
    if now is None:
        now = now_moscow()

    # Если первый респ ещё не наступил — возвращаем его
    if now <= first_spawn_at:
        return first_spawn_at

    # Первый респ уже в прошлом
    elapsed = (now - first_spawn_at).total_seconds() / 60  # в минутах
    
    # Если прошло меньше 2 минут — НЕ догоняем, возвращаем first_spawn_at как есть.
    # tick_notifications поймает его в окне [-1, +1] минута и отправит уведомление.
    if elapsed <= 2:
        return first_spawn_at

    # Догоняем до ближайшего будущего респа
    # Считаем, сколько полных циклов respawn_minutes прошло с first_spawn_at
    k = math.ceil(elapsed / respawn_minutes)
    next_spawn = first_spawn_at + timedelta(minutes=k * respawn_minutes)
    
    return next_spawn
