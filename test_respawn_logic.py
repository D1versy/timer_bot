#!/usr/bin/env python3
"""Тестовый скрипт для проверки логики next_spawn_at."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.services import next_spawn_at, MOSCOW

TZ = MOSCOW


def test_scenario(name: str, **kwargs):
    """Запустить тест и вывести результат."""
    result = next_spawn_at(**kwargs)
    now = kwargs.get('now')
    if result:
        if now:
            delta = (result - now).total_seconds() / 60
            print(f"[{name}]")
            print(f"  → Следующий респ: {result.strftime('%d.%m.%Y %H:%M')}")
            print(f"  → До респа: {delta:.1f} мин")
        else:
            print(f"[{name}]")
            print(f"  → Следующий респ: {result.strftime('%d.%m.%Y %H:%M')}")
    else:
        print(f"[{name}]")
        print(f"  → Респ: None")
    print()


def main():
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ ЛОГИКИ РЕСПАУНОВ")
    print("=" * 60)
    print()

    # Базовое время для тестов
    now = datetime(2026, 2, 4, 12, 0, tzinfo=TZ)  # 04.02.2026 12:00
    restart_past = datetime(2026, 2, 4, 9, 0, tzinfo=TZ)  # рестарт в 09:00 (3 часа назад)
    restart_future = datetime(2026, 2, 4, 13, 0, tzinfo=TZ)  # рестарт в 13:00 (через час)

    print(f"Текущее время (now): {now.strftime('%d.%m.%Y %H:%M')}")
    print(f"Рестарт в прошлом: {restart_past.strftime('%d.%m.%Y %H:%M')}")
    print(f"Рестарт в будущем: {restart_future.strftime('%d.%m.%Y %H:%M')}")
    print()

    # ===== Сценарий 1: Рестарт в прошлом, босс с first =====
    print("-" * 60)
    print("Сценарий 1: Рестарт 09:00, first=60мин (10:00), resp=2h")
    print("Ожидание: босс появился в 10:00, потом 12:00, 14:00... -> след. респ 14:00")
    print("-" * 60)
    test_scenario(
        "first=60m, resp=2h, restart=09:00, now=12:00",
        last_kill_at=None,
        server_restart_at=restart_past,
        first_spawn_minutes=60,  # первое появление в 10:00
        respawn_minutes=120,  # 2 часа
        now=now,
    )

    # ===== Сценарий 2: Рестарт в прошлом, босс без first =====
    print("-" * 60)
    print("Сценарий 2: Рестарт 09:00, first=None (сразу), resp=2h")
    print("Ожидание: босс появился в 09:00, потом 11:00, 13:00... -> след. респ 13:00")
    print("-" * 60)
    test_scenario(
        "first=None, resp=2h, restart=09:00, now=12:00",
        last_kill_at=None,
        server_restart_at=restart_past,
        first_spawn_minutes=None,
        respawn_minutes=120,
        now=now,
    )

    # ===== Сценарий 3: Рестарт в будущем =====
    print("-" * 60)
    print("Сценарий 3: Рестарт 13:00 (в будущем), first=30m, resp=2h")
    print("Ожидание: первое появление в 13:30")
    print("-" * 60)
    test_scenario(
        "first=30m, resp=2h, restart=13:00, now=12:00",
        last_kill_at=None,
        server_restart_at=restart_future,
        first_spawn_minutes=30,
        respawn_minutes=120,
        now=now,
    )

    # ===== Сценарий 4: После ручного /kill =====
    print("-" * 60)
    print("Сценарий 4: last_kill_at=11:30, resp=2h")
    print("Ожидание: следующий респ 13:30 (kill + resp)")
    print("-" * 60)
    killed_at = datetime(2026, 2, 4, 11, 30, tzinfo=TZ)
    test_scenario(
        "last_kill_at=11:30, resp=2h",
        last_kill_at=killed_at,
        server_restart_at=restart_past,
        first_spawn_minutes=60,
        respawn_minutes=120,
        now=now,
    )

    # ===== Сценарий 5: Босс с first=1m (быстрый) =====
    print("-" * 60)
    print("Сценарий 5: Рестарт 09:00, first=1m, resp=3h")
    print("Ожидание: first в 09:01, потом 12:01, 15:01... -> след. респ 12:01")
    print("-" * 60)
    test_scenario(
        "first=1m, resp=3h, restart=09:00, now=12:00",
        last_kill_at=None,
        server_restart_at=restart_past,
        first_spawn_minutes=1,
        respawn_minutes=180,
        now=now,
    )

    # ===== Сценарий 6: Рестарт очень давно =====
    print("-" * 60)
    print("Сценарий 6: Рестарт 02.02.2026 09:00 (2 дня назад), first=6h, resp=8h")
    print("Ожидание: правильно 'догнать' до ближайшего будущего респа")
    print("-" * 60)
    old_restart = datetime(2026, 2, 2, 9, 0, tzinfo=TZ)
    test_scenario(
        "first=6h, resp=8h, restart=02.02 09:00, now=04.02 12:00",
        last_kill_at=None,
        server_restart_at=old_restart,
        first_spawn_minutes=360,  # 6 часов
        respawn_minutes=480,  # 8 часов
        now=now,
    )

    # ===== Сценарий 7: Босс без first при /restart now =====
    print("-" * 60)
    print("Сценарий 7: /restart now, босс без first (появляется сразу), resp=3h")
    print("now = 12:00:00, restart = 11:59:30 (30 сек назад)")
    print("Ожидание: респ = 11:59:30 (чтобы tick_notifications поймал его)")
    print("-" * 60)
    restart_just_now = datetime(2026, 2, 4, 11, 59, 30, tzinfo=TZ)
    test_scenario(
        "first=None, resp=3h, restart=11:59:30, now=12:00:00",
        last_kill_at=None,
        server_restart_at=restart_just_now,
        first_spawn_minutes=None,
        respawn_minutes=180,
        now=now,
    )

    # ===== Сценарий 8: Босс без first, прошло 1.5 минуты =====
    print("-" * 60)
    print("Сценарий 8: /restart, босс без first, прошло 1.5 минуты")
    print("now = 12:00, restart = 11:58:30")
    print("Ожидание: респ = 11:58:30 (ещё в пределах 2 минут, не догоняем)")
    print("-" * 60)
    restart_1_5_min_ago = datetime(2026, 2, 4, 11, 58, 30, tzinfo=TZ)
    test_scenario(
        "first=None, resp=3h, restart=11:58:30, now=12:00",
        last_kill_at=None,
        server_restart_at=restart_1_5_min_ago,
        first_spawn_minutes=None,
        respawn_minutes=180,
        now=now,
    )

    # ===== Сценарий 9: Босс без first, прошло 5 минут =====
    print("-" * 60)
    print("Сценарий 9: /restart, босс без first, прошло 5 минут")
    print("now = 12:00, restart = 11:55")
    print("Ожидание: догоняем до 14:55 (11:55 + 3h)")
    print("-" * 60)
    restart_5_min_ago = datetime(2026, 2, 4, 11, 55, 0, tzinfo=TZ)
    test_scenario(
        "first=None, resp=3h, restart=11:55, now=12:00",
        last_kill_at=None,
        server_restart_at=restart_5_min_ago,
        first_spawn_minutes=None,
        respawn_minutes=180,
        now=now,
    )

    print("=" * 60)
    print("ТЕСТЫ ЗАВЕРШЕНЫ")
    print("=" * 60)


if __name__ == "__main__":
    main()
