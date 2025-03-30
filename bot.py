import os
import logging
from dotenv import load_dotenv
import asyncio
import nest_asyncio
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
    ContextTypes,
    JobQueue,
)
from datetime import datetime, timedelta
from collections import Counter

nest_asyncio.apply()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

SERVICE, DATE, TIME = range(3)
SETTINGS, MANAGE_SERVICES, MANAGE_TIMES = range(3, 6)

DB_PATH = "bookings.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            service TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        )
    ''')
    
    cursor.execute("PRAGMA table_info(bookings)")
    columns = [col[1] for col in cursor.fetchall()]
    if "time" not in columns:
        cursor.execute("ALTER TABLE bookings ADD COLUMN time TEXT NOT NULL DEFAULT '00:00'")
    
    cursor.execute("DROP INDEX IF EXISTS idx_date_time")
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_name TEXT NOT NULL UNIQUE,
            setting_value TEXT NOT NULL
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO settings (setting_name, setting_value) VALUES (?, ?)", ("slot_limit", "2"))
    cursor.execute("INSERT OR IGNORE INTO settings (setting_name, setting_value) VALUES (?, ?)", ("reminders_enabled", "1"))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL UNIQUE
        )
    ''')
    initial_services = ["Массаж", "Маникюр", "Стрижка"]
    for service in initial_services:
        cursor.execute("INSERT OR IGNORE INTO services (service_name) VALUES (?)", (service,))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS time_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_slot TEXT NOT NULL UNIQUE
        )
    ''')
    initial_times = ["10:00", "12:00", "14:00", "16:00"]
    for time in initial_times:
        cursor.execute("INSERT OR IGNORE INTO time_slots (time_slot) VALUES (?)", (time,))
    
    conn.commit()
    conn.close()

def get_slot_limit():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'slot_limit'")
    result = cursor.fetchone()
    conn.close()
    return int(result[0]) if result else 2

def are_reminders_enabled():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'reminders_enabled'")
    result = cursor.fetchone()
    conn.close()
    return int(result[0]) == 1 if result else True

def get_services():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT service_name FROM services")
    services = [row[0] for row in cursor.fetchall()]
    conn.close()
    return services

def get_time_slots():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT time_slot FROM time_slots")
    times = [row[0] for row in cursor.fetchall()]
    conn.close()
    return times

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Записаться"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на услуги.", reply_markup=reply_markup)
    return ConversationHandler.END

async def record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"Кнопка 'Записаться' нажата пользователем {user.full_name}")
    
    context.user_data.clear()
    context.user_data['state'] = SERVICE
    
    services = get_services()
    if not services:
        await update.message.reply_text("Нет доступных услуг. Обратитесь к администратору.")
        return ConversationHandler.END
    
    keyboard = [[InlineKeyboardButton(service, callback_data=service) for service in services[i:i+2]] for i in range(0, len(services), 2)]
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service = query.data
    
    if service == "back_to_menu":
        return await back_to_menu(update, context)
    
    context.user_data["service"] = service
    context.user_data['state'] = DATE
    logger.info(f"Выбрана услуга: {service} пользователем {query.from_user.full_name}")
    
    dates = []
    for i in range(7):
        date = datetime.now() + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        dates.append(InlineKeyboardButton(date_str, callback_data=date_str))
    keyboard = [dates, [InlineKeyboardButton("Назад", callback_data="back_to_services")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Выбрана услуга: {service}\nВыбери дату:", reply_markup=reply_markup)
    return DATE

async def date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    date = query.data
    
    if date == "back_to_services":
        return await back_to_services(update, context)
    
    context.user_data["date"] = date
    context.user_data['state'] = TIME
    logger.info(f"Выбрана дата: {date}")
    
    all_times = get_time_slots()
    if not all_times:
        await query.edit_message_text("Нет доступных временных слотов. Обратитесь к администратору.")
        return ConversationHandler.END
    
    try:
        times = [InlineKeyboardButton(time, callback_data=time) for time in all_times]
        keyboard = [[*times[i:i+3]] for i in range(0, len(times), 3)]
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_date")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Выбрана услуга: {context.user_data['service']}\nДата: {date}\nВыбери время:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке слотов: {e}")
        await query.edit_message_text("Ошибка при загрузке доступных слотов.")
        return ConversationHandler.END
    
    return TIME

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    admin_id = job.data["admin_id"]
    service = job.data["service"]
    date = job.data["date"]
    time = job.data["time"]
    await context.bot.send_message(chat_id, f"Напоминание: завтра у вас {service} на {date} в {time}!")
    await context.bot.send_message(admin_id, f"Клиент записан на {service} на {date} в {time} завтра.")

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    time = query.data
    
    if time == "back_to_date":
        return await back_to_date(update, context)
    
    service = context.user_data["service"]
    date = context.user_data["date"]
    user = update.effective_user
    logger.info(f"Попытка записи: {service} на {date} в {time} для {user.full_name}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    SLOT_LIMIT = get_slot_limit()
    cursor.execute("SELECT COUNT(*) FROM bookings WHERE date = ? AND time = ?", (date, time))
    current_bookings = cursor.fetchone()[0]
    logger.info(f"Текущих записей на {date} {time}: {current_bookings}, лимит: {SLOT_LIMIT}")
    
    if current_bookings >= SLOT_LIMIT:
        logger.info(f"Слот {date} {time} достиг лимита ({SLOT_LIMIT})")
        await query.edit_message_text(
            f"Слот {time} на {date} занят (лимит {SLOT_LIMIT} записи). Выберите другое время."
        )
        conn.close()
        return await date(update, context)
    
    try:
        cursor.execute(
            "INSERT INTO bookings (user_name, service, date, time) VALUES (?, ?, ?, ?)",
            (user.full_name, service, date, time)
        )
        conn.commit()
        logger.info("Запись успешно добавлена в базу")
        
        if are_reminders_enabled():
            current_year = datetime.now().year
            date_time_str = f"{date}.{current_year} {time}"
            date_obj = datetime.strptime(date_time_str, "%d.%m.%Y %H:%M")
            reminder_time = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            
            days_until = (date_obj.date() - reminder_time.date()).days
            if days_until > 0:
                reminder_time += timedelta(days=days_until - 1)
                context.job_queue.run_once(
                    send_reminder,
                    when=reminder_time,
                    data={"chat_id": user.id, "admin_id": ADMIN_ID, "service": service, "date": date, "time": time}
                )
                logger.info(f"Напоминание запланировано на {reminder_time}")
        
        keyboard = [
            [InlineKeyboardButton("Записаться снова", callback_data="restart")],
            [InlineKeyboardButton("Оплатить", callback_data="pay")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"✅ Запись оформлена!\n🗓 Услуга: {service}\n📅 Дата: {date}\n⏰ Время: {time}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при записи: {e}")
        await query.edit_message_text("Ошибка при записи данных.")
    finally:
        conn.close()
    
    return ConversationHandler.END

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fake_order_id = f"{query.from_user.id}{int(datetime.now().timestamp())}"
    payment_link = f"https://example.com/pay?order={fake_order_id}"
    keyboard = [[InlineKeyboardButton("Перейти к оплате", url=payment_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Перенаправление на платёжный шлюз...\nДля оплаты перейдите по ссылке: {payment_link}\n(Функция оплаты в разработке)",
        reply_markup=reply_markup
    )

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['state'] = SERVICE
    
    services = get_services()
    keyboard = [[InlineKeyboardButton(service, callback_data=service) for service in services[i:i+2]] for i in range(0, len(services), 2)]
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    keyboard = [["Записаться"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await query.edit_message_text("Вы вернулись в главное меню.", reply_markup=reply_markup)
    return ConversationHandler.END

async def back_to_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('date', None)
    context.user_data['state'] = SERVICE
    
    services = get_services()
    keyboard = [[InlineKeyboardButton(service, callback_data=service) for service in services[i:i+2]] for i in range(0, len(services), 2)]
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def back_to_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service = context.user_data["service"]
    context.user_data['state'] = DATE
    
    dates = []
    for i in range(7):
        date = datetime.now() + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        dates.append(InlineKeyboardButton(date_str, callback_data=date_str))
    keyboard = [dates, [InlineKeyboardButton("Назад", callback_data="back_to_services")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Выбрана услуга: {service}\nВыбери дату:", reply_markup=reply_markup)
    return DATE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Запись отменена.")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"Кнопка 'Помощь' нажата пользователем {user.full_name}")
    await update.message.reply_text(
        "Я бот для записи на услуги!\n"
        "Доступные команды:\n"
        "- Записаться: выбрать услугу, дату и время\n"
        "- Помощь: это сообщение\n"
        "- /admin: панель администратора (для админа)"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    
    logger.info(f"Админ {user_id} открыл панель управления")
    keyboard = [
        [InlineKeyboardButton("Посмотреть все записи", callback_data="admin_view")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("Настройки", callback_data="admin_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Админ-панель:", reply_markup=reply_markup)

async def view_all_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_name, service, date, time FROM bookings")
        all_bookings = cursor.fetchall()
        conn.close()
        
        if not all_bookings:
            await query.edit_message_text("Записей нет.")
            return
        
        keyboard = []
        for booking in all_bookings:
            booking_id, user_name, service, date, time = booking
            keyboard.append([
                InlineKeyboardButton(f"{user_name}: {service} на {date} в {time}", callback_data="noop"),
                InlineKeyboardButton("Удалить", callback_data=f"admin_delete_{booking_id}")
            ])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_admin")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Все записи:", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при просмотре записей: {e}")
        await query.edit_message_text("Ошибка при загрузке записей.")

async def delete_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    booking_id = int(query.data.split("_")[2])
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_name, service, date, time FROM bookings WHERE id = ?", (booking_id,))
        booking = cursor.fetchone()
        
        if not booking:
            await query.edit_message_text("Запись не найдена.")
            conn.close()
            return
        
        user_name, service, date, time = booking
        cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()
        
        keyboard = [[InlineKeyboardButton("Назад", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Запись удалена: {user_name} - {service} на {date} в {time}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении записи: {e}")
        await query.edit_message_text("Ошибка при удалении записи.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bookings")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT service, COUNT(*) FROM bookings GROUP BY service")
        service_counts = cursor.fetchall()
        service_stats = "\n".join([f"{service}: {count}" for service, count in service_counts]) if service_counts else "Нет записей"
        
        cursor.execute("SELECT date, COUNT(*) as cnt FROM bookings GROUP BY date ORDER BY cnt DESC LIMIT 1")
        popular_day = cursor.fetchone()
        popular_day_str = f"{popular_day[0]} ({popular_day[1]} записей)" if popular_day else "Нет данных"
        
        conn.close()
        
        stats_text = (
            f"📊 Статистика:\n"
            f"Всего записей: {total}\n"
            f"По услугам:\n{service_stats}\n"
            f"Самый популярный день: {popular_day_str}"
        )
        keyboard = [[InlineKeyboardButton("Назад", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(stats_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при подсчёте статистики: {e}")
        await query.edit_message_text("Ошибка при подсчёте статистики.")

async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    keyboard = [
        [InlineKeyboardButton("Посмотреть все записи", callback_data="admin_view")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("Настройки", callback_data="admin_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Админ-панель:", reply_markup=reply_markup)

async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    SLOT_LIMIT = get_slot_limit()
    reminders_status = "Вкл" if are_reminders_enabled() else "Выкл"
    keyboard = [
        [InlineKeyboardButton(f"Лимит слотов: {SLOT_LIMIT}", callback_data="set_slot_limit")],
        [InlineKeyboardButton("Управление услугами", callback_data="manage_services")],
        [InlineKeyboardButton("Управление временем", callback_data="manage_times")],
        [InlineKeyboardButton(f"Напоминания: {reminders_status}", callback_data="toggle_reminders")],
        [InlineKeyboardButton("Назад", callback_data="back_to_admin")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Настройки бота:", reply_markup=reply_markup)

async def toggle_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        current_status = are_reminders_enabled()
        new_status = 0 if current_status else 1
        cursor.execute("UPDATE settings SET setting_value = ? WHERE setting_name = 'reminders_enabled'", (str(new_status),))
        conn.commit()
        conn.close()
        
        status_text = "включены" if new_status else "выключены"
        logger.info(f"Админ {user_id} изменил статус напоминаний на: {status_text}")
        
        SLOT_LIMIT = get_slot_limit()
        reminders_status = "Вкл" if new_status else "Выкл"
        keyboard = [
            [InlineKeyboardButton(f"Лимит слотов: {SLOT_LIMIT}", callback_data="set_slot_limit")],
            [InlineKeyboardButton("Управление услугами", callback_data="manage_services")],
            [InlineKeyboardButton("Управление временем", callback_data="manage_times")],
            [InlineKeyboardButton(f"Напоминания: {reminders_status}", callback_data="toggle_reminders")],
            [InlineKeyboardButton("Назад", callback_data="back_to_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Напоминания {status_text}.", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при переключении напоминаний: {e}")
        await query.edit_message_text("Ошибка при переключении напоминаний.")

async def set_slot_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return SETTINGS
    
    context.user_data['admin_state'] = SETTINGS
    await query.edit_message_text("Введите новый лимит слотов (целое число, например, 1, 3, 5):")
    return SETTINGS

async def save_slot_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Доступ запрещён.")
        return ConversationHandler.END
    
    try:
        new_limit = int(update.message.text)
        if new_limit <= 0:
            await update.message.reply_text("Лимит должен быть положительным числом. Попробуйте снова:")
            return SETTINGS
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE settings SET setting_value = ? WHERE setting_name = 'slot_limit'", (str(new_limit),))
        conn.commit()
        conn.close()
        
        reminders_status = "Вкл" if are_reminders_enabled() else "Выкл"
        keyboard = [
            [InlineKeyboardButton(f"Лимит слотов: {new_limit}", callback_data="set_slot_limit")],
            [InlineKeyboardButton("Управление услугами", callback_data="manage_services")],
            [InlineKeyboardButton("Управление временем", callback_data="manage_times")],
            [InlineKeyboardButton(f"Напоминания: {reminders_status}", callback_data="toggle_reminders")],
            [InlineKeyboardButton("Назад", callback_data="back_to_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"Лимит слотов обновлён: {new_limit}", reply_markup=reply_markup)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите целое число. Попробуйте снова:")
        return SETTINGS
    except Exception as e:
        logger.error(f"Ошибка при сохранении лимита: {e}")
        await update.message.reply_text("Ошибка при сохранении лимита.")
        return ConversationHandler.END

async def manage_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    services = get_services()
    keyboard = [[InlineKeyboardButton(service, callback_data=f"delete_service_{service}")] for service in services]
    keyboard.append([InlineKeyboardButton("Добавить услугу", callback_data="add_service")])
    keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_settings")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Текущие услуги (нажмите для удаления):", reply_markup=reply_markup)

async def add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return MANAGE_SERVICES
    
    context.user_data['admin_state'] = MANAGE_SERVICES
    await query.edit_message_text("Введите название новой услуги:")
    return MANAGE_SERVICES

async def save_new_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Доступ запрещён.")
        return ConversationHandler.END
    
    new_service = update.message.text.strip()
    if not new_service:
        await update.message.reply_text("Название услуги не может быть пустым. Попробуйте снова:")
        return MANAGE_SERVICES
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO services (service_name) VALUES (?)", (new_service,))
        conn.commit()
        conn.close()
        
        services = get_services()
        keyboard = [[InlineKeyboardButton(service, callback_data=f"delete_service_{service}")] for service in services]
        keyboard.append([InlineKeyboardButton("Добавить услугу", callback_data="add_service")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_settings")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"Услуга '{new_service}' добавлена.", reply_markup=reply_markup)
        return ConversationHandler.END
    except sqlite3.IntegrityError:
        await update.message.reply_text("Такая услуга уже существует. Попробуйте другое название:")
        return MANAGE_SERVICES
    except Exception as e:
        logger.error(f"Ошибка при добавлении услуги: {e}")
        await update.message.reply_text("Ошибка при добавлении услуги.")
        return ConversationHandler.END

async def delete_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    service_to_delete = query.data.replace("delete_service_", "")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM services WHERE service_name = ?", (service_to_delete,))
        conn.commit()
        conn.close()
        
        services = get_services()
        keyboard = [[InlineKeyboardButton(service, callback_data=f"delete_service_{service}")] for service in services]
        keyboard.append([InlineKeyboardButton("Добавить услугу", callback_data="add_service")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_settings")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Текущие услуги (нажмите для удаления):", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при удалении услуги: {e}")
        await query.edit_message_text("Ошибка при удалении услуги.")

async def manage_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    times = get_time_slots()
    keyboard = [[InlineKeyboardButton(time, callback_data=f"delete_time_{time}")] for time in times]
    keyboard.append([InlineKeyboardButton("Добавить время", callback_data="add_time")])
    keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_settings")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Текущие временные слоты (нажмите для удаления):", reply_markup=reply_markup)

async def add_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return MANAGE_TIMES
    
    context.user_data['admin_state'] = MANAGE_TIMES
    await query.edit_message_text("Введите новый временной слот (формат HH:MM, например, 18:00):")
    return MANAGE_TIMES

async def save_new_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Доступ запрещён.")
        return ConversationHandler.END
    
    new_time = update.message.text.strip()
    try:
        datetime.strptime(new_time, "%H:%M")
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Используйте HH:MM (например, 18:00). Попробуйте снова:")
        return MANAGE_TIMES
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO time_slots (time_slot) VALUES (?)", (new_time,))
        conn.commit()
        conn.close()
        
        times = get_time_slots()
        keyboard = [[InlineKeyboardButton(time, callback_data=f"delete_time_{time}")] for time in times]
        keyboard.append([InlineKeyboardButton("Добавить время", callback_data="add_time")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_settings")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"Время '{new_time}' добавлено.", reply_markup=reply_markup)
        return ConversationHandler.END
    except sqlite3.IntegrityError:
        await update.message.reply_text("Такое время уже существует. Попробуйте другое:")
        return MANAGE_TIMES
    except Exception as e:
        logger.error(f"Ошибка при добавлении времени: {e}")
        await update.message.reply_text("Ошибка при добавлении времени.")
        return ConversationHandler.END

async def delete_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещён.")
        return
    
    time_to_delete = query.data.replace("delete_time_", "")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM time_slots WHERE time_slot = ?", (time_to_delete,))
        conn.commit()
        conn.close()
        
        times = get_time_slots()
        keyboard = [[InlineKeyboardButton(time, callback_data=f"delete_time_{time}")] for time in times]
        keyboard.append([InlineKeyboardButton("Добавить время", callback_data="add_time")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="admin_settings")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Текущие временные слоты (нажмите для удаления):", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при удалении времени: {e}")
        await query.edit_message_text("Ошибка при удалении времени.")

def main():
    init_db()
    
    application = Application.builder().token(TOKEN).job_queue(JobQueue()).build()
    application.job_queue.start()
    
    record_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(Записаться)$"), record),
            CallbackQueryHandler(restart, pattern="^restart$")
        ],
        states={
            SERVICE: [CallbackQueryHandler(service)],
            DATE: [CallbackQueryHandler(date)],
            TIME: [CallbackQueryHandler(get_time)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
            CallbackQueryHandler(back_to_services, pattern="^back_to_services$"),
            CallbackQueryHandler(back_to_date, pattern="^back_to_date$"),
        ],
        allow_reentry=True
    )
    
    admin_settings_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(set_slot_limit, pattern="^set_slot_limit$"),
            CallbackQueryHandler(add_service, pattern="^add_service$"),
            CallbackQueryHandler(add_time, pattern="^add_time$")
        ],
        states={
            SETTINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_slot_limit)],
            MANAGE_SERVICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_service)],
            MANAGE_TIMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_time)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex(r"^(Помощь)$"), help_command))  # Добавляем обработчик для "Помощь"
    application.add_handler(record_handler)
    application.add_handler(admin_settings_handler)
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(view_all_bookings, pattern="^admin_view$"))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(delete_booking, pattern="^admin_delete_"))
    application.add_handler(CallbackQueryHandler(back_to_admin, pattern="^back_to_admin$"))
    application.add_handler(CallbackQueryHandler(admin_settings, pattern="^admin_settings$"))
    application.add_handler(CallbackQueryHandler(manage_services, pattern="^manage_services$"))
    application.add_handler(CallbackQueryHandler(delete_service, pattern="^delete_service_"))
    application.add_handler(CallbackQueryHandler(manage_times, pattern="^manage_times$"))
    application.add_handler(CallbackQueryHandler(delete_time, pattern="^delete_time_"))
    application.add_handler(CallbackQueryHandler(toggle_reminders, pattern="^toggle_reminders$"))
    application.add_handler(CallbackQueryHandler(pay, pattern="^pay$"))
    
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
