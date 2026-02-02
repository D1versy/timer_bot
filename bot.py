#!/usr/bin/env python3
"""
Telegram-бот для отслеживания респаунов рейд-боссов L2M.
Время по Simferopol (UTC+3). Токен из .env файла, админы из admins.txt.
"""
import os
import re
import logging
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.db import SessionLocal
from app.models import Boss, KillLog, ServerState
from app.services import now_moscow, next_spawn_at, MOSCOW

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMINS_FILE = Path(__file__).parent / "admins.txt"
TZ = ZoneInfo("Europe/Moscow")

_subscribers: set[int] = set()


def load_admins() -> set[str]:
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
    with open(ADMINS_FILE, "w", encoding="utf-8") as f:
        f.write("# Список админов\n")
        for admin in sorted(admins):
            f.write(f"{admin}\n")


def is_admin(user) -> bool:
    admins = load_admins()
    if str(user.id) in admins:
        return True
    if user.username and f"@{user.username}" in admins:
        return True
    return False


def _naive_tz(dt: datetime) -> datetime:
    return dt.astimezone(TZ).replace(tzinfo=None)


def _aware_tz(dt: datetime | None) -> datetime | None:
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
        db.add(ServerState(id=1, server_restart_at=val, notification_intervals="15,5,1"))
    else:
        row.server_restart_at = val
    db.commit()


def get_notification_intervals(db) -> list[int]:
    """Получить интервалы уведомлений (в минутах)."""
    row = db.query(ServerState).filter(ServerState.id == 1).first()
    if not row or not row.notification_intervals:
        return [15, 5, 1]
    try:
        return sorted([int(x) for x in row.notification_intervals.split(",")], reverse=True)
    except:
        return [15, 5, 1]


def set_notification_intervals(db, intervals: list[int]):
    row = db.query(ServerState).filter(ServerState.id == 1).first()
    if not row:
        db.add(ServerState(id=1, server_restart_at=None, notification_intervals=",".join(map(str, intervals))))
    else:
        row.notification_intervals = ",".join(map(str, intervals))
    db.commit()


def boss_next_spawn(boss: Boss, server_restart_at: datetime | None) -> datetime | None:
    return next_spawn_at(
        _aware_tz(boss.last_kill_at),
        server_restart_at,
        boss.first_spawn_minutes,
        boss.respawn_minutes,
    )


def format_time_short(dt: datetime | None) -> str:
    """HH:MM или --:--"""
    if dt is None:
        return "--:--"
    return dt.strftime("%H:%M")


def format_respawn_interval(minutes: int) -> str:
    if minutes == 0:
        return "0h"
    elif minutes >= 1440:
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
    """Список боссов: HH:MM | ID | имя | шанс% | resp 10h | first 5h"""
    restart = get_server_restart(db)
    bosses = db.query(Boss).filter(Boss.is_active).order_by(Boss.id).all()
    rows = []
    for b in bosses:
        nxt = boss_next_spawn(b, restart)
        time_str = format_time_short(nxt)
        interval_str = format_respawn_interval(b.respawn_minutes)
        first_str = format_respawn_interval(b.first_spawn_minutes) if b.first_spawn_minutes is not None else "—"
        rows.append((nxt, b.id, b.name, b.spawn_chance_percent, time_str, interval_str, first_str))
    rows.sort(key=lambda x: (x[0] is None, x[0] or datetime.max.replace(tzinfo=TZ)))
    
    lines = []
    for _, bid, name, chance, time_str, interval_str, first_str in rows:
        lines.append(f"{time_str} | {bid} | {name} | {chance}% | resp {interval_str} | first {first_str}")
    return "\n".join(lines) if lines else "Нет активных боссов."


def make_kill_button(boss_id: int, boss_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Босс убит", callback_data=f"kill_confirm_{boss_id}")]
    ])


def make_confirm_buttons(boss_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Босс убит", callback_data=f"kill_do_{boss_id}"),
            InlineKeyboardButton("Отмена", callback_data=f"kill_cancel_{boss_id}"),
        ]
    ])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
🤖 **Команды бота**

📋 **/list**
Показывает список боссов с абсолютным временем респауна.
Формат: `HH:MM | ID | имя | шанс% | resp 10h | first 6h`

━━━━━━━━━━━━━━━━━━━━

🔄 **/restart [время]**
Админы: Задаёт время рестарта сервера.

Примеры:
• `/restart` — рестарт «сейчас»
• `/restart 14:30` — сегодня 14:30
• `/restart 01.02.2026 14:30` — точная дата

━━━━━━━━━━━━━━━━━━━━

⚔️ **/kill <ID> [время]**
Админы: Фиксирует убийство босса.

Примеры:
• `/kill 22` — убийство «сейчас»
• `/kill 22 17:30` — сегодня/вчера
• `/kill 22 02.02.2026 13:59` — точное время

━━━━━━━━━━━━━━━━━━━━

⚙️ **/settings**
Админы: Панель управления.

━━━━━━━━━━━━━━━━━━━━

💡 *Время: Simferopol (UTC+3)*
"""
    try:
        await update.message.reply_text(help_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка в cmd_help: {e}", exc_info=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_help(update, context)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    _subscribers.add(chat_id)
    
    db = SessionLocal()
    try:
        text = format_list_text(db)
        await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в cmd_list: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка при получении списка: {str(e)}")
    finally:
        db.close()


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            next_time = format_time_short(nxt)
            
            await query.edit_message_text(
                f"✅ Убийство [{boss.id}] {boss.name} зафиксировано.\n"
                f"Следующий респ: {next_time}"
            )
        finally:
            db.close()
    
    elif data.startswith("kill_cancel_"):
        await query.edit_message_text("Отменено.")


def parse_restart_arg(s: str) -> datetime | None:
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
        # Сбрасываем last_kill_at для всех боссов при рестарте
        db.query(Boss).update({Boss.last_kill_at: None})
        db.commit()
        
        # Находим боссов с first <= 5 минут и отправляем уведомления
        fast_bosses = db.query(Boss).filter(
            Boss.is_active,
            Boss.first_spawn_minutes != None,
            Boss.first_spawn_minutes <= 5
        ).all()
        
        text = f"✅ Время рестарта установлено: {dt.strftime('%d.%m.%Y %H:%M')}\n🔄 Все таймеры боссов сброшены\n\n{format_list_text(db)}"
        await update.message.reply_text(text)
        
        # Отправляем уведомления для боссов с быстрым first всем подписчикам
        if fast_bosses and _subscribers:
            for boss in fast_bosses:
                spawn_time = dt + timedelta(minutes=boss.first_spawn_minutes or 0)
                time_str = format_time_short(spawn_time)
                message = f"🔴 Босс После рестарта через {boss.first_spawn_minutes}м:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
                markup = make_kill_button(boss.id, boss.name)
                
                for chat_id in list(_subscribers):
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=markup)
                    except Exception as e:
                        logger.error(f"Не удалось отправить в {chat_id}: {e}")
                        _subscribers.discard(chat_id)
    finally:
        db.close()


def parse_kill_datetime(s: str) -> datetime | None:
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
        next_time = format_time_short(nxt)
        
        text = f"✅ Убийство [{boss.id}] {boss.name} зафиксировано: {killed_at.strftime('%d.%m.%Y %H:%M')}\nСледующий респ: {next_time}"
        await update.message.reply_text(text)
    finally:
        db.close()


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            time_str = test_time.strftime("%H:%M")
            text = f"{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
            await update.message.reply_text(text, reply_markup=make_kill_button(boss.id, boss.name))
    finally:
        db.close()


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    text = """
⚙️ **Settings (Admin only)**

**1) Добавить босса**
`/boss_add Тест 50% 12h 0h`
• 12h = респ после /kill (> 0!)
• 0h = респ после /restart (1 мин)

**2) Удалить босса**
`/boss_del 48`

**3) Редактировать босса**
`/boss_edit 48 Тест 50% 12h 0h`

**4) Настроить уведомления**
`/notifications 15 5 1` — за 15, 5, 1 мин

**5) Админы**
`/admin_add @username`
`/admin_del @username`
`/admin_list`

**6) Резервная копия БД**
`/backup` — скачать файл БД
📎 Отправьте файл .db — восстановить БД

━━━━━━━━━━━━━━━━━━━━

⚠️ **Логика таймеров:**
• `/restart` — сбрасывает все таймеры, счёт по first
• `/kill` — записывает время, счёт по resp
• Форматы: `10h`, `30m`, `1d`, `2h30m`
"""
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    admins = load_admins()
    if not admins:
        await update.message.reply_text("Список админов пуст.")
        return
    
    admin_lines = []
    for admin in sorted(admins):
        escaped = admin.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
        admin_lines.append(f"• {escaped}")
    
    text = "👮 **Список админов:**\n\n" + "\n".join(admin_lines)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет файл базы данных в чат."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    from app.db import DB_PATH
    
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("❌ Файл базы данных не найден.")
        return
    
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"app_backup_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.db",
                caption="📦 Резервная копия базы данных"
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке backup: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def handle_db_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает загруженный файл БД для восстановления."""
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    document = update.message.document
    if not document:
        return
    
    # Проверяем, что файл похож на БД
    filename = document.file_name or ""
    if not filename.endswith(".db"):
        await update.message.reply_text("⚠️ Отправьте файл с расширением .db для восстановления базы данных.")
        return
    
    from app.db import DB_PATH
    
    try:
        # Создаём резервную копию текущей БД
        backup_path = DB_PATH + f".backup_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}"
        if os.path.exists(DB_PATH):
            import shutil
            shutil.copy2(DB_PATH, backup_path)
        
        # Скачиваем файл
        file = await document.get_file()
        await file.download_to_drive(DB_PATH)
        
        await update.message.reply_text(
            f"✅ База данных восстановлена из файла: {filename}\n"
            f"📦 Резервная копия старой БД: {os.path.basename(backup_path)}\n\n"
            f"⚠️ Перезапустите бота для применения изменений!"
        )
    except Exception as e:
        logger.error(f"Ошибка при восстановлении БД: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


def parse_duration(s: str) -> int | None:
    """Парсит строку времени. Возвращает минуты или None если не указано.
    Поддерживает: 0, 0h, 0m, 1d, 2h30m и т.д.
    """
    if s is None:
        return None
    s = s.strip().lower()
    if not s:
        return None
    
    # Проверяем явный ноль: "0", "0h", "0m"
    if s in ("0", "0h", "0m"):
        return 0
    
    total = 0
    m = re.search(r"(\d+)d", s)
    if m:
        total += int(m.group(1)) * 1440
    m = re.search(r"(\d+)h", s)
    if m:
        total += int(m.group(1)) * 60
    m = re.search(r"(\d+)m", s)
    if m:
        total += int(m.group(1))
    
    # Если нашли хоть что-то, возвращаем (может быть 0)
    if re.search(r"\d", s):
        return total
    return None


async def cmd_boss_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 4:
        await update.message.reply_text(
            "Использование:\n"
            "`/boss_add <Имя> <Шанс%> <респ> [первое]`\n\n"
            "• `респ` — время после убийства (> 0)\n"
            "• `первое` — время после рестарта (0h = 1m)\n\n"
            "Примеры:\n"
            "`/boss_add Тест 50% 12h 0h` — респ 12ч, после рестарта 1м\n"
            "`/boss_add Тест 50% 6h 2h` — респ 6ч, после рестарта 2ч",
            parse_mode="Markdown"
        )
        return
    
    name = parts[1]
    chance_str = parts[2].replace("%", "")
    respawn_str = parts[3]
    first_str = parts[4] if len(parts) > 4 else None
    
    try:
        chance = int(chance_str)
        respawn_min = parse_duration(respawn_str)
        first_min = parse_duration(first_str) if first_str else None
        if respawn_min is None or respawn_min <= 0:
            raise ValueError("Invalid duration")
        # 0h после рестарта = 1 минута (чтобы сразу пришло уведомление)
        if first_min is not None and first_min == 0:
            first_min = 1
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат.\n\n"
            "• Респ после убийства: > 0\n"
            "• Респ после рестарта: 0h = 1m\n\n"
            "Пример: `/boss_add Тест 50% 12h 0h`",
            parse_mode="Markdown"
        )
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
            first_spawn_minutes=first_min,
            respawn_minutes=respawn_min,
            is_active=True,
            last_kill_at=None,
        )
        db.add(boss)
        db.commit()
        db.refresh(boss)
        
        interval_str = format_respawn_interval(respawn_min)
        first_display = format_respawn_interval(first_min) if first_min is not None else "—"
        
        await update.message.reply_text(
            f"✅ Босс добавлен:\n"
            f"ID {boss.id} | {boss.name} | {chance}%\n"
            f"Респ после убийства: {interval_str}\n"
            f"Респ после рестарта: {first_display}"
        )
    finally:
        db.close()


async def cmd_boss_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 5:
        await update.message.reply_text("Использование: /boss_edit <id> <Имя> <Шанс%> <респ> [первое]\nПример: /boss_edit 48 Чертуба 50% 12h 5h")
        return
    
    try:
        boss_id = int(parts[1])
        name = parts[2]
        chance = int(parts[3].replace("%", ""))
        respawn_min = parse_duration(parts[4])
        first_min = parse_duration(parts[5]) if len(parts) > 5 else None
        if respawn_min is None or respawn_min <= 0:
            raise ValueError("Invalid duration")
        # 0h после рестарта = 1 минута (чтобы сразу пришло уведомление)
        if first_min is not None and first_min == 0:
            first_min = 1
    except ValueError:
        await update.message.reply_text("Неверный формат. Респ после убийства должен быть > 0.")
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
        boss.first_spawn_minutes = first_min
        db.commit()
        
        interval_str = format_respawn_interval(respawn_min)
        first_display = format_respawn_interval(first_min) if first_min is not None else "—"
        
        await update.message.reply_text(
            f"✅ Босс [{boss_id}] обновлён:\n"
            f"{name} | {chance}%\n"
            f"Респ после убийства: {interval_str}\n"
            f"Респ после рестарта: {first_display}"
        )
    finally:
        db.close()


async def cmd_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user):
        await update.message.reply_text("⛔ Недостаточно прав")
        return
    
    parts = (update.message.text or "").strip().split()
    if len(parts) < 2:
        await update.message.reply_text("Использование: /notifications <минуты>\nПример: /notifications 20 15 5 1")
        return
    
    try:
        intervals = [int(x) for x in parts[1:]]
        if not intervals:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Все значения должны быть числами.")
        return
    
    db = SessionLocal()
    try:
        set_notification_intervals(db, intervals)
        await update.message.reply_text(f"✅ Уведомления настроены: за {', '.join(map(str, sorted(intervals, reverse=True)))} минут до респа.")
    finally:
        db.close()


async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


_sent_notifications: set[tuple[int, str, int]] = set()  # (boss_id, spawn_key, minutes_before)


def _spawn_key(nt: datetime) -> str:
    return nt.strftime("%Y-%m-%d %H:%M") if nt else ""


async def tick_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _subscribers:
        return

    db = SessionLocal()
    try:
        restart = get_server_restart(db)
        bosses = db.query(Boss).filter(Boss.is_active).all()
        intervals = get_notification_intervals(db)
        now = datetime.now(TZ)

        for boss in bosses:
            nxt = boss_next_spawn(boss, restart)
            if nxt is None:
                continue
            
            key_base = _spawn_key(nxt)
            delta_m = (nxt - now).total_seconds() / 60

            # Пропускаем боссов с респом в прошлом (они уже появились, ждём /kill)
            if delta_m <= -1:
                continue
            
            if -1 < delta_m <= 1:
                # Появление (в пределах 1 минуты)
                notification_key = (boss.id, key_base, 0)
                if notification_key not in _sent_notifications:
                    _sent_notifications.add(notification_key)
                    time_str = format_time_short(nxt)
                    message = f"🔴 Босс появился:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
                    markup = make_kill_button(boss.id, boss.name)
                    
                    for chat_id in list(_subscribers):
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=markup)
                        except Exception as e:
                            logger.error(f"Не удалось отправить в {chat_id}: {e}")
                            _subscribers.discard(chat_id)
            else:
                # Проверяем интервалы уведомлений (только для будущих респов)
                for interval in intervals:
                    if (interval - 1) <= delta_m <= (interval + 1):
                        notification_key = (boss.id, key_base, interval)
                        if notification_key not in _sent_notifications:
                            _sent_notifications.add(notification_key)
                            time_str = format_time_short(nxt)
                            message = f"⚠️ Через {interval} минут{'у' if interval == 1 else ''} респ:\n{time_str} | {boss.id} | {boss.name} | {boss.spawn_chance_percent}%"
                            markup = make_kill_button(boss.id, boss.name)
                            
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
    app.add_handler(CommandHandler("notifications", cmd_notifications))
    app.add_handler(CommandHandler("admin_add", cmd_admin_add))
    app.add_handler(CommandHandler("admin_del", cmd_admin_del))
    app.add_handler(CommandHandler("admin_list", cmd_admin_list))
    app.add_handler(CommandHandler("backup", cmd_backup))
    
    # Обработчик загрузки файлов БД
    app.add_handler(MessageHandler(filters.Document.ALL, handle_db_restore))
    
    app.add_handler(CallbackQueryHandler(callback_handler))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(tick_notifications, interval=60, first=10)

    logger.info("✅ Бот запущен. Токен из .env, админы из admins.txt")
    
    # Graceful shutdown при Ctrl+C
    def signal_handler(sig, frame):
        logger.info("⚠️ Получен сигнал остановки. Завершаю работу...")
        try:
            app.stop_running()
        except:
            pass
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("⚠️ Остановка бота по Ctrl+C...")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        raise
    finally:
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    main()
