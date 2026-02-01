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
    - Если рестарт ПОСЛЕ последнего килла → считаем от рестарта (по first_spawn_minutes)
    - Если килл ПОСЛЕ рестарта → считаем от килла (по respawn_minutes)
    - Если нет килла → считаем от рестарта
    """
    # Защита от бесконечного цикла при respawn_minutes <= 0
    if respawn_minutes <= 0:
        return None
    
    now = now_moscow()

    # Определяем, от чего считать: от рестарта или от килла
    use_restart = False
    use_kill = False
    
    if last_kill_at is not None and server_restart_at is not None:
        # Есть и килл, и рестарт — сравниваем, что было позже
        if server_restart_at > last_kill_at:
            # Рестарт после килла → считаем от рестарта
            use_restart = True
        else:
            # Килл после рестарта → считаем от килла
            use_kill = True
    elif last_kill_at is not None:
        # Только килл, нет рестарта
        use_kill = True
    elif server_restart_at is not None:
        # Только рестарт, нет килла
        use_restart = True
    else:
        # Нет ни килла, ни рестарта
        return None

    if use_kill:
        # Считаем от килла по respawn_minutes
        next_ = last_kill_at + timedelta(minutes=respawn_minutes)
        while next_ <= now:
            next_ += timedelta(minutes=respawn_minutes)
        return next_

    if use_restart:
        # Считаем от рестарта
        if first_spawn_minutes is not None:
            # Есть first_spawn_minutes → первое появление через это время после рестарта
            first_spawn = server_restart_at + timedelta(minutes=first_spawn_minutes)
            if first_spawn > now:
                return first_spawn
            # Если первое появление уже прошло — считаем дальше по respawn_minutes
            next_ = first_spawn
            while next_ <= now:
                next_ += timedelta(minutes=respawn_minutes)
            return next_
        else:
            # Нет first_spawn_minutes → появляется сразу после рестарта, дальше по интервалу
            next_ = server_restart_at
            while next_ <= now:
                next_ += timedelta(minutes=respawn_minutes)
            return next_

    return None
