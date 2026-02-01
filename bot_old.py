#!/usr/bin/env python3
"""
Telegram-бот для отслеживания респаунов рейд-боссов L2M.
Время по Simferopol (UTC+3). Токен из .env файла, админы из admins.txt.
"""
import os
import re
import logging
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from app.db import SessionLocal
from app.models import Boss, KillLog, ServerState
from app.services import now_moscow, next_spawn_at, MOSCOW

# Загрузка .env (BOT_TOKEN)
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMINS_FILE = Path(__file__).parent / "admins.txt"
TZ = ZoneInfo("Europe/Simferopol")  # UTC+3

# Хранилище подписчиков для уведомлений (chat_id)
_subscribers: set[int] = set()


def load_admins() -> set[str]:
    """Читает admins.txt, возвращает set ID и username (с @)."""
    if not ADMINS_FILE.exists():
        ADMINS_FILE.write_text("# Список админов\n", encoding="utf-8")
        return set()
    admins = set()
    with open(ADMINS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            admins.add(line)
    return admins


def save_admins(admins: set[str]):
    """Сохраняет список админов в admins.txt."""
    with open(ADMINS_FILE, "w", encoding="utf-8") as f:
        f.write("# Список админов\n")
        for admin in sorted(admins):
            f.write(f"{admin}\n")


def is_admin(user) -> bool:
    """Проверяет, является ли пользователь админом."""
    admins = load_admins()
    if str(user.id) in admins:
        return True
    if user.username and f"@{user.username}" in admins:
        return True
    return False


def _naive_tz(dt: datetime) -> datetime:
    """Сохранить в БД: без tz, считаем что Simferopol."""
    return dt.astimezone(TZ).replace(tzinfo=None)


def _aware_tz(dt: datetime | None) -> datetime | None:
    """Прочитать из БД: считать время Simferopol."""
    if dt is None:
        return None
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def get_server_restart(db) -> datetime | None:
    row = db.query(ServerState).filter(ServerState.id == 1).first()
    return _aware_tz(row.server_restart_at) if row and row.server_restart_at else None


def set_server_restart(db, at: datetime | None):
    val = _naive_tz(at) if at else None
    row = db.query(ServerState).filter(ServerState.id == 1).first()
    if not row:
        db.add(ServerState(id=1, server_restart_at=val))
    else:
        row.server_restart_at = val
    db.commit()


def boss_next_spawn(boss: Boss, server_restart_at: datetime | None) -> datetime | None:
    return next_spawn_at(
        _aware_tz(boss.last_kill_at),
        server_restart_at,
        boss.first_spawn_minutes,
        boss.respawn_minutes,
    )


def format_time_absolute(dt: datetime | None) -> str:
    """HH:MM:SS или --:--:--"""
    if dt is None:
        return "--:--:--"
    return dt.strftime("%H:%M:%S")


def format_respawn_interval(minutes: int) -> str:
    """Форматирует интервал респауна: 10h, 1d, 30m."""
    if minutes >= 1440:
        days = minutes // 1440
        return f"{days}d"
    elif minutes >= 60:
        hours = minutes // 60
        remainder = minutes % 60
        if remainder > 0:
            return f"{hours}h{remainder}m"
        return f"{hours}h"
    else:
        return f"{minutes}m"


def format_list_text(db) -> str:
    """Список боссов: HH:MM:SS | ID | имя | шанс% | resp 10h"""
    restart = get_server_restart(db)
    bosses = db.query(Boss).filter(Boss.is_active).order_by(Boss.id).all()
    rows = []
    for b in bosses:
        nxt = boss_next_spawn(b, restart)
        time_str = format_time_absolute(nxt)
        interval_str = format_respawn_interval(b.respawn_minutes)
        rows.append((nxt, b.id, b.name, b.spawn_chance_percent, time_str, interval_str))
    # Сортируем по времени респа (None в конец)
    rows.sort(key=lambda x: (x[0] is None, x[0] or datetime.max.replace(tzinfo=TZ)))
    
    lines = []
    for _, bid, name, chance, time_str, interval_str in rows:
        lines.append(f"{time_str} | {bid} | {name} | {chance}% | resp {interval_str}")
    return "\n".join(lines) if lines else "Нет активных боссов."


def make_kill_button(boss_id: int, boss_name: str) -> InlineKeyboardMarkup:
    """Кнопка 'Босс убит' под боссом."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Босс убит", callback_data=f"kill_confirm_{boss_id}")]
    ])


def make_confirm_buttons(boss_id: int) -> InlineKeyboardMarkup:
    """Подтверждение убийства."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Босс убит", callback_data=f"kill_do_{boss_id}"),
            InlineKeyboardButton("Отмена", callback_data=f"kill_cancel_{boss_id}"),
        ]
    ])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Справка по командам."""
    help_text = """
🤖 **Команды бота**

📋 **/list**
Кто может: любой пользователь

Показывает список боссов с абсолютным временем респауна.
Формат: `HH:MM:SS | ID | имя | шанс%`

У каждого босса кнопка "Босс убит" для быстрой фиксации.

━━━━━━━━━━━━━━━━━━━━

🔄 **/restart [время]**
Кто может: только админы

Задаёт время рестарта сервера и пересчитывает таймеры.

Примеры:
• `/restart` или `/restart now` — рестарт «сейчас»
• `/restart 14:30` — сегодня 14:30
• `/restart 01.02.2026 14:30` — точная дата

━━━━━━━━━━━━━━━━━━━━

⚔️ **/kill <ID> [время]**
Кто может: только админы

Фиксирует убийство босса.

Примеры:
• `/kill 22` — убийство «сейчас»
• `/kill 22 17:30` — сегодня/вчера в 17:30
• `/kill 22 02.02.2026 13:59` — точное время

━━━━━━━━━━━━━━━━━━━━

⚙️ **/settings**
Кто может: только админы

Админ-панель для управления боссами и админами.

━━━━━━━━━━━━━━━━━━━━

💡 *Все команды работают по времени Simferopol (UTC+3)*
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start = /help."""
    await cmd_help(update, context)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список боссов одним сообщением без кнопок."""
    chat_id = update.effective_chat.id
    _subscribers.add(chat_id)
    
    db = SessionLocal()
    try:
        text = format_list_text(db)
        await update.message.reply_text(text)
    finally:
        db.close()


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатий на кнопки."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("kill_confirm_"):
        boss_id = int(data.split("_")[2])
        await query.edit_message_reply_markup(reply_markup=make_confirm_buttons(boss_id))
    
    elif data.startswith("kill_do_"):
        boss_id = int(data.split("_")[2])
        db = SessionLocal()
        try:
            boss = db.query(Boss).filter(Boss.id == boss_id).first()
            if not boss:
                await query.edit_message_text("Босс не найден.")
                return
            
            now = datetime.now(TZ)
            boss.last_kill_at = _naive_tz(now)
            db.add(KillLog(boss_id=boss.id, killed_at=boss.last_kill_at, note="button kill"))
            db.commit()
            
            restart = get_server_restart(db)
            nxt = boss_next_spawn(boss, restart)
            next_time = format_time_absolute(nxt)
            
            await query.edit_message_text(
                f"✅ Убийство [{boss.id}] {boss.name} зафиксировано.\n"
                f"Следующий респ: {next_time}"
            )
        finally:
            db.close()
    
    elif data.startswith("kill_cancel_"):
        await query.edit_message_text("Отменено.")


def parse_restart_arg(s: str) -> datetime | None:
    """Парсит дату/время рестарта: DD.MM.YYYY HH:MM или HH:MM или 'now'."""
    s = (s or "").strip()
    if s.lower() == "now" or not s:
        return datetime.now(TZ)
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})", s)
    if m:
        d, mo, y, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        return datetime(y, mo, d, h, mi, tzinfo=TZ)
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        now = datetime.now(TZ)
        today = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if today <= now:
            today += timedelta(days=1)
        return today
    return None


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Установить время рестарта сервера (только админы)."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    args = (update.message.text or "").split(maxsplit=1)
    arg = args[1].strip() if len(args) > 1 else "now"
    dt = parse_restart_arg(arg)
    if dt is None:
        await update.message.reply_text("Формат: /restart [DD.MM.YYYY HH:MM] или /restart HH:MM или /restart now")
        return
    db = SessionLocal()
    try:
        set_server_restart(db, dt)
        text = f"✅ Время рестарта установлено: {dt.strftime('%d.%m.%Y %H:%M')}\n\n{format_list_text(db)}"
        await update.message.reply_text(text)
    finally:
        db.close()


def parse_kill_datetime(s: str) -> datetime | None:
    """DD.MM.YYYY HH:MM или HH:MM."""
    s = (s or "").strip()
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})", s)
    if m:
        d, mo, y, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        return datetime(y, mo, d, h, mi, tzinfo=TZ)
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        now = datetime.now(TZ)
        today = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if today > now:
            return today - timedelta(days=1)
        return today
    return None


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Зафиксировать убийство босса (только админы)."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/kill <ID> — убийство сейчас\n"
            "/kill <ID> HH:MM — время сегодня/вчера\n"
            "/kill <ID> DD.MM.YYYY HH:MM — точное время"
        )
        return
    try:
        boss_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("ID босса должен быть числом.")
        return

    db = SessionLocal()
    try:
        boss = db.query(Boss).filter(Boss.id == boss_id).first()
        if not boss:
            await update.message.reply_text("Босс с таким ID не найден.")
            return

        if len(parts) == 2:
            killed_at = datetime.now(TZ)
        else:
            killed_at = parse_kill_datetime(" ".join(parts[2:]))
            if killed_at is None:
                await update.message.reply_text("Неверный формат времени. Примеры: 14:30 или 01.02.2026 14:30")
                return

        boss.last_kill_at = _naive_tz(killed_at)
        db.add(KillLog(boss_id=boss.id, killed_at=boss.last_kill_at, note=None))
        db.commit()
        
        restart = get_server_restart(db)
        nxt = boss_next_spawn(boss, restart)
        next_time = format_time_absolute(nxt)
        
        text = f"✅ Убийство [{boss.id}] {boss.name} зафиксировано: {killed_at.strftime('%d.%m.%Y %H:%M')}\nСледующий респ: {next_time}"
        await update.message.reply_text(text)
    finally:
        db.close()


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тестовая команда: /test <bossId> отправляет 3 сообщения с now+1/2/3 мин + кнопки."""
    parts = (update.message.text or "").strip().split()
    if len(parts) < 2:
        await update.message.reply_text("Использование: /test <bossId>")
        return
    
    try:
        boss_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("ID босса должен быть числом.")
        return
    
    db = SessionLocal()
    try:
        boss = db.query(Boss).filter(Boss.id == boss_id).first()
        if not boss:
            await update.message.reply_text("Босс не найден.")
            return
        
        now = datetime.now(TZ)
        for i in range(1, 4):
            test_time = now + timedelta(minutes=i)
            time_str = test_time.strftime("%H:%M:%S")
            text = f"{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
            await update.message.reply_text(text, reply_markup=make_kill_button(boss.id, boss.name))
    finally:
        db.close()


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ-панель."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    text = """
⚙️ **Settings (Admin only)**

**1) Добавить босса**
`/boss_add Чертуба 50% 10h`
`/boss_add Медуза 100% 1d`

**2) Удалить босса**
`/boss_del 48`

**3) Редактировать босса**
`/boss_edit 48 Чертуба 50% 12h`

**4) Добавить админа**
`/admin_add @username`
`/admin_add 123456789`

**5) Удалить админа**
`/admin_del @username`

**6) Список админов**
`/admin_list`

━━━━━━━━━━━━━━━━━━━━

Форматы времени: `10h`, `30m`, `1d`, `2h30m`
"""
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список админов."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    admins = load_admins()
    if not admins:
        await update.message.reply_text("Список админов пуст.")
        return
    
    # Escape @ для Markdown
    admin_lines = []
    for admin in sorted(admins):
        # Экранируем @ и другие спецсимволы Markdown
        escaped = admin.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
        admin_lines.append(f"• {escaped}")
    
    text = "👮 **Список админов:**\n\n" + "\n".join(admin_lines)
    await update.message.reply_text(text, parse_mode="Markdown")


def parse_duration(s: str) -> int | None:
    """Парсит '10h', '1d', '30m', '2h30m' в минуты."""
    s = s.strip().lower()
    total = 0
    # 1d, 2d
    m = re.search(r"(\d+)d", s)
    if m:
        total += int(m.group(1)) * 1440
    # 10h, 2h
    m = re.search(r"(\d+)h", s)
    if m:
        total += int(m.group(1)) * 60
    # 30m
    m = re.search(r"(\d+)m", s)
    if m:
        total += int(m.group(1))
    return total if total > 0 else None


async def cmd_boss_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Добавить босса: /boss_add Чертуба 50% 10h"""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 4:
        await update.message.reply_text("Использование: /boss_add <Имя> <Шанс%> <время>\nПример: /boss_add Чертуба 50% 10h")
        return
    
    name = parts[1]
    chance_str = parts[2].replace("%", "")
    duration_str = parts[3]
    
    try:
        chance = int(chance_str)
        respawn_min = parse_duration(duration_str)
        if respawn_min is None:
            raise ValueError("Invalid duration")
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: /boss_add Чертуба 50% 10h")
        return
    
    db = SessionLocal()
    try:
        exists = db.query(Boss).filter(Boss.name == name).first()
        if exists:
            await update.message.reply_text(f"Босс '{name}' уже существует (ID {exists.id}).")
            return
        
        boss = Boss(
            name=name,
            spawn_chance_percent=chance,
            first_spawn_minutes=None,
            respawn_minutes=respawn_min,
            is_active=True,
            last_kill_at=None,
        )
        db.add(boss)
        db.commit()
        db.refresh(boss)
        
        # Форматируем интервал для вывода
        if respawn_min >= 1440:
            interval = f"{respawn_min // 1440} день"
        elif respawn_min >= 60:
            interval = f"{respawn_min // 60}ч"
        else:
            interval = f"{respawn_min}м"
        
        await update.message.reply_text(
            f"✅ Босс добавлен:\n"
            f"--:--:-- | {boss.id} | {boss.name} | {boss.spawn_chance_percent}% | {interval}"
        )
    finally:
        db.close()


async def cmd_boss_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удалить босса: /boss_del <id>"""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 2:
        await update.message.reply_text("Использование: /boss_del <id>")
        return
    
    try:
        boss_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    
    db = SessionLocal()
    try:
        boss = db.query(Boss).filter(Boss.id == boss_id).first()
        if not boss:
            await update.message.reply_text("Босс не найден.")
            return
        
        db.delete(boss)
        db.commit()
        await update.message.reply_text(f"✅ Босс [{boss_id}] {boss.name} удалён.")
    finally:
        db.close()


async def cmd_boss_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Редактировать босса: /boss_edit <id> Имя Шанс% время"""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 5:
        await update.message.reply_text("Использование: /boss_edit <id> <Имя> <Шанс%> <время>\nПример: /boss_edit 48 Чертуба 50% 12h")
        return
    
    try:
        boss_id = int(parts[1])
        name = parts[2]
        chance = int(parts[3].replace("%", ""))
        respawn_min = parse_duration(parts[4])
        if respawn_min is None:
            raise ValueError("Invalid duration")
    except ValueError:
        await update.message.reply_text("Неверный формат.")
        return
    
    db = SessionLocal()
    try:
        boss = db.query(Boss).filter(Boss.id == boss_id).first()
        if not boss:
            await update.message.reply_text("Босс не найден.")
            return
        
        boss.name = name
        boss.spawn_chance_percent = chance
        boss.respawn_minutes = respawn_min
        db.commit()
        
        await update.message.reply_text(f"✅ Босс [{boss_id}] обновлён: {name} {chance}% {respawn_min}м")
    finally:
        db.close()


async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Добавить админа: /admin_add @username или /admin_add 123456"""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 2:
        await update.message.reply_text("Использование: /admin_add @username")
        return
    
    new_admin = parts[1]
    admins = load_admins()
    
    if new_admin in admins:
        await update.message.reply_text(f"{new_admin} уже является админом.")
        return
    
    admins.add(new_admin)
    save_admins(admins)
    await update.message.reply_text(f"✅ {new_admin} добавлен в админы.")


async def cmd_admin_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удалить админа: /admin_del @username"""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 2:
        await update.message.reply_text("Использование: /admin_del @username")
        return
    
    del_admin = parts[1]
    admins = load_admins()
    
    if del_admin not in admins:
        await update.message.reply_text(f"{del_admin} не найден в админах.")
        return
    
    admins.remove(del_admin)
    save_admins(admins)
    await update.message.reply_text(f"✅ {del_admin} удалён из админов.")


# ——— Уведомления: 15, 5, 1 мин и объявление появления ———
_sent_notifications: set[tuple[int, str]] = set()


def _spawn_key(nt: datetime) -> str:
    return nt.strftime("%Y-%m-%d %H:%M") if nt else ""


async def tick_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверка и отправка уведомлений всем подписчикам."""
    if not _subscribers:
        return

    db = SessionLocal()
    try:
        restart = get_server_restart(db)
        bosses = db.query(Boss).filter(Boss.is_active).all()
        now = datetime.now(TZ)

        for boss in bosses:
            nxt = boss_next_spawn(boss, restart)
            if nxt is None:
                continue
            key_base = _spawn_key(nxt)
            delta_m = (nxt - now).total_seconds() / 60

            message = None
            notification_key = None

            if delta_m <= 0:
                # Появление
                notification_key = (boss.id, key_base + "0")
                if notification_key not in _sent_notifications:
                    time_str = format_time_absolute(nxt)
                    message = f"🔴 Босс появился:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
                    # Новый цикл
                    boss.last_kill_at = _naive_tz(now)
                    db.add(KillLog(boss_id=boss.id, killed_at=boss.last_kill_at, note="авто: появление"))
                    db.commit()
            elif 0.5 <= delta_m <= 1.5:
                notification_key = (boss.id, key_base + "1")
                if notification_key not in _sent_notifications:
                    time_str = format_time_absolute(nxt)
                    message = f"⚠️ Через 1 минуту респ:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
            elif 4 <= delta_m <= 6:
                notification_key = (boss.id, key_base + "5")
                if notification_key not in _sent_notifications:
                    time_str = format_time_absolute(nxt)
                    message = f"⚠️ Через 5 минут респ:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
            elif 14 <= delta_m <= 16:
                notification_key = (boss.id, key_base + "15")
                if notification_key not in _sent_notifications:
                    time_str = format_time_absolute(nxt)
                    message = f"⚠️ Через 15 минут респ:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"

            if message and notification_key:
                _sent_notifications.add(notification_key)
                # Кнопка "Босс убит" во всех уведомлениях 15/5/1 мин
                markup = make_kill_button(boss.id, boss.name) if delta_m > 0 else None
                
                for chat_id in list(_subscribers):
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=markup)
                    except Exception as e:
                        logger.error(f"Не удалось отправить в {chat_id}: {e}")
                        _subscribers.discard(chat_id)
    finally:
        db.close()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ Задайте BOT_TOKEN в файле .env")

    from app.db import ensure_db_exists
    ensure_db_exists()

    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("boss_add", cmd_boss_add))
    app.add_handler(CommandHandler("boss_del", cmd_boss_del))
    app.add_handler(CommandHandler("boss_edit", cmd_boss_edit))
    app.add_handler(CommandHandler("admin_add", cmd_admin_add))
    app.add_handler(CommandHandler("admin_del", cmd_admin_del))
    app.add_handler(CommandHandler("admin_list", cmd_admin_list))
    
    app.add_handler(CallbackQueryHandler(callback_handler))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(tick_notifications, interval=60, first=10)

    logger.info("✅ Бот запущен. Токен из .env, админы из admins.txt")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
