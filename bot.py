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
        logger.info("Добавляем колонку time в таблицу bookings")
        cursor.execute("ALTER TABLE bookings ADD COLUMN time TEXT NOT NULL DEFAULT '00:00'")
    
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_date_time ON bookings (date, time)")
        logger.info("Создан уникальный индекс на date и time")
    except sqlite3.OperationalError as e:
        logger.warning(f"Индекс уже существует или ошибка: {str(e)}")
    
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на услуги.", reply_markup=reply_markup)
    return ConversationHandler.END

async def record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Вызвана функция record для пользователя {update.message.from_user.full_name}")
    context.user_data.clear()  # Очищаем данные для нового цикла
    services = [
        InlineKeyboardButton("Массаж", callback_data="Массаж"),
        InlineKeyboardButton("Маникюр", callback_data="Маникюр"),
        InlineKeyboardButton("Стрижка", callback_data="Стрижка"),
    ]
    reply_markup = InlineKeyboardMarkup([services])
    await update.message.reply_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service = query.data
    context.user_data["service"] = service
    logger.info(f"Выбрана услуга: {service} пользователем {query.from_user.full_name}")
    
    dates = []
    for i in range(7):
        date = datetime.now() + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        dates.append(InlineKeyboardButton(date_str, callback_data=date_str))
    reply_markup = InlineKeyboardMarkup([dates])
    await query.edit_message_text(f"Выбрана услуга: {service}\nВыбери дату:", reply_markup=reply_markup)
    return DATE

async def date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    date = query.data
    context.user_data["date"] = date
    logger.info(f"Выбрана дата: {date}")
    
    all_times = ["10:00", "12:00", "14:00", "16:00"]
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT time FROM bookings WHERE date = ?", (date,))
        booked_times = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        available_times = [t for t in all_times if t not in booked_times]
        
        if not available_times:
            await query.edit_message_text(f"На {date} нет свободных слотов. Выберите другую дату.")
            return DATE
        
        times = [InlineKeyboardButton(time, callback_data=time) for time in available_times]
        reply_markup = InlineKeyboardMarkup([times])
        await query.edit_message_text(
            f"Выбрана услуга: {context.user_data['service']}\nДата: {date}\nВыбери время:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при проверке слотов: {type(e).__name__}: {str(e)}")
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
    service = context.user_data["service"]
    date = context.user_data["date"]
    user = update.effective_user
    logger.info(f"Попытка записи: {service} на {date} в {time} для {user.full_name}")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bookings (user_name, service, date, time) VALUES (?, ?, ?, ?)",
            (user.full_name, service, date, time)
        )
        conn.commit()
        conn.close()
        
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
        
        keyboard = [[InlineKeyboardButton("Записаться снова", callback_data="restart")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"✅ Запись оформлена!\n🗓 Услуга: {service}\n📅 Дата: {date}\n⏰ Время: {time}",
            reply_markup=reply_markup
        )
    except sqlite3.IntegrityError:
        logger.error(f"Попытка записать дубликат: {date} {time}")
        await query.edit_message_text(f"Слот {time} на {date} уже занят. Выберите другое время.")
        return await date(update, context)
    except Exception as e:
        logger.error(f"Ошибка при записи: {type(e).__name__}: {str(e)}")
        await query.edit_message_text("Ошибка при записи данных.")
    return ConversationHandler.END

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info(f"Вызван restart для пользователя {query.from_user.full_name}")
    context.user_data.clear()  # Очищаем данные для нового цикла
    
    # Эмулируем новый вход в ConversationHandler
    services = [
        InlineKeyboardButton("Массаж", callback_data="Массаж"),
        InlineKeyboardButton("Маникюр", callback_data="Маникюр"),
        InlineKeyboardButton("Стрижка", callback_data="Стрижка"),
    ]
    reply_markup = InlineKeyboardMarkup([services])
    await query.edit_message_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запись отменена.")
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bookings")
        total = cursor.fetchone()[0]
        conn.close()
        await update.message.reply_text(f"📊 Всего записей: {total}")
    except Exception as e:
        logger.error(f"Ошибка в /stats: {type(e).__name__}: {str(e)}")
        await update.message.reply_text("Ошибка при подсчете записей.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я бот для записи на услуги!\n"
        "Доступные команды:\n"
        "- Записаться: выбрать услугу, дату и время\n"
        "- Посмотреть записи: список твоих записей\n"
        "- Помощь: это сообщение"
    )

async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, service, date, time FROM bookings WHERE user_name = ?", (user.full_name,))
        user_bookings = cursor.fetchall()
        conn.close()
        
        if not user_bookings:
            await update.message.reply_text(f"У вас нет записей. Ваше имя: {user.full_name}")
            return
        
        keyboard = []
        for booking in user_bookings:
            booking_id, service, date, time = booking
            callback_data = f"cancel_{booking_id}"
            keyboard.append([
                InlineKeyboardButton(f"{service} на {date} в {time}", callback_data="noop"),
                InlineKeyboardButton("Отменить", callback_data=callback_data)
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Ваши записи:", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при просмотре записей: {type(e).__name__}: {str(e)}")
        await update.message.reply_text("Ошибка при загрузке записей.")

async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    booking_id = int(query.data.split("_")[1])
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()
        
        await query.edit_message_text("Запись успешно отменена!")
    except Exception as e:
        logger.error(f"Ошибка при отмене записи: {type(e).__name__}: {str(e)}")
        await query.edit_message_text("Ошибка при отмене записи.")

def main():
    init_db()
    
    application = Application.builder().token(TOKEN).job_queue(JobQueue()).build()
    application.job_queue.start()
    
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(Записаться)$"), record),
            CallbackQueryHandler(restart, pattern="^restart$")  # Добавляем restart как точку входа
        ],
        states={
            SERVICE: [CallbackQueryHandler(service)],
            DATE: [CallbackQueryHandler(date)],
            TIME: [CallbackQueryHandler(get_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.Regex(r"^(Помощь)$"), help_command))
    application.add_handler(MessageHandler(filters.Regex(r"^(Посмотреть записи)$"), view_bookings))
    application.add_handler(CallbackQueryHandler(cancel_booking, pattern="^cancel_"))
    
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
