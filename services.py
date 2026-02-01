from datetime import datetime, timedelta

def calc_next_window(last_kill_at: datetime | None, min_m: int, max_m: int):
    if not last_kill_at:
        return None, None
    return (
        last_kill_at + timedelta(minutes=min_m),
        last_kill_at + timedelta(minutes=max_m),
    )