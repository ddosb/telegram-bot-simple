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
SLOT_LIMIT = 2  # Максимум 2 записи на один слот времени

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
    
    cursor.execute("DROP INDEX IF EXISTS idx_date_time")
    logger.info("Уникальный индекс idx_date_time удалён для поддержки SLOT_LIMIT")
    
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Записаться"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на услуги.", reply_markup=reply_markup)
    return ConversationHandler.END

async def record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"Кнопка 'Записаться' нажата пользователем {user.full_name}, текущее состояние: {context.user_data.get('state', 'None')}")
    
    context.user_data.clear()
    context.user_data['state'] = SERVICE
    
    services = [
        [InlineKeyboardButton("Массаж", callback_data="Массаж"),
         InlineKeyboardButton("Маникюр", callback_data="Маникюр"),
         InlineKeyboardButton("Стрижка", callback_data="Стрижка")],
        [InlineKeyboardButton("Назад", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(services)
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
    
    all_times = ["10:00", "12:00", "14:00", "16:00"]
    
    try:
        times = [InlineKeyboardButton(time, callback_data=time) for time in all_times]
        keyboard = [[*times], [InlineKeyboardButton("Назад", callback_data="back_to_date")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Выбрана услуга: {context.user_data['service']}\nДата: {date}\nВыбери время:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке слотов: {type(e).__name__}: {str(e)}")
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
    
    cursor.execute("SELECT COUNT(*) FROM bookings WHERE date = ? AND time = ?", (date, time))
    current_bookings = cursor.fetchone()[0]
    logger.info(f"Текущих записей на {date} {time}: {current_bookings}")
    
    if current_bookings >= SLOT_LIMIT:
        logger.info(f"Слот {date} {time} достиг лимита ({SLOT_LIMIT})")
        await query.edit_message_text(
            f"Слот {time} на {date} занят (лимит {SLOT_LIMIT} записи). Выберите другое время."
        )
        conn.close()
        return await date(update, context)
    
    try:
        logger.info(f"Вставка записи: {user.full_name}, {service}, {date}, {time}")
        cursor.execute(
            "INSERT INTO bookings (user_name, service, date, time) VALUES (?, ?, ?, ?)",
            (user.full_name, service, date, time)
        )
        conn.commit()
        logger.info("Запись успешно добавлена в базу")
        
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
        logger.error(f"Ошибка при записи: {type(e).__name__}: {str(e)}")
        await query.edit_message_text("Ошибка при записи данных.")
    finally:
        conn.close()
    
    return ConversationHandler.END

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info(f"Пользователь {query.from_user.full_name} нажал 'Оплатить'")
    
    fake_order_id = f"{query.from_user.id}{int(datetime.now().timestamp())}"
    payment_link = f"https://example.com/pay?order={fake_order_id}"
    
    keyboard = [
        [InlineKeyboardButton("Перейти к оплате", url=payment_link)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Перенаправление на платёжный шлюз...\n"
        f"Для оплаты перейдите по ссылке: {payment_link}\n"
        f"(Функция оплаты в разработке)",
        reply_markup=reply_markup
    )

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info(f"Вызван restart для пользователя {query.from_user.full_name}")
    context.user_data.clear()
    context.user_data['state'] = SERVICE
    
    services = [
        [InlineKeyboardButton("Массаж", callback_data="Массаж"),
         InlineKeyboardButton("Маникюр", callback_data="Маникюр"),
         InlineKeyboardButton("Стрижка", callback_data="Стрижка")],
        [InlineKeyboardButton("Назад", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(services)
    await query.edit_message_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"Пользователь {query.from_user.full_name} вернулся в главное меню")
    context.user_data.clear()
    keyboard = [["Записаться"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await query.edit_message_text("Вы вернулись в главное меню.", reply_markup=reply_markup)
    return ConversationHandler.END

async def back_to_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"Пользователь {query.from_user.full_name} вернулся к выбору услуг")
    context.user_data.pop('date', None)
    context.user_data['state'] = SERVICE
    
    services = [
        [InlineKeyboardButton("Массаж", callback_data="Массаж"),
         InlineKeyboardButton("Маникюр", callback_data="Маникюр"),
         InlineKeyboardButton("Стрижка", callback_data="Стрижка")],
        [InlineKeyboardButton("Назад", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(services)
    await query.edit_message_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

async def back_to_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    service = context.user_data["service"]
    logger.info(f"Пользователь {query.from_user.full_name} вернулся к выбору даты")
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
    logger.info(f"Отмена записи пользователем {update.message.from_user.full_name}")
    context.user_data.clear()
    await update.message.reply_text("Запись отменена.")
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    await send_stats(update.message, context)

async def send_stats(message_or_query, context):
    logger.info("Начало подсчёта статистики")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        logger.info("Запрос общего количества записей")
        cursor.execute("SELECT COUNT(*) FROM bookings")
        total = cursor.fetchone()[0]
        
        logger.info("Запрос записей по услугам")
        cursor.execute("SELECT service, COUNT(*) FROM bookings GROUP BY service")
        service_counts = cursor.fetchall()
        service_stats = "\n".join([f"{service}: {count}" for service, count in service_counts]) if service_counts else "Нет записей"
        
        logger.info("Запрос самого популярного дня")
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
        logger.info("Статистика успешно сформирована")
        await message_or_query.edit_message_text(stats_text)
    except Exception as e:
        logger.error(f"Ошибка при подсчёте статистики: {type(e).__name__}: {str(e)}")
        await message_or_query.edit_message_text("Ошибка при подсчёте статистики.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я бот для записи на услуги!\n"
        "Доступные команды:\n"
        "- Записаться: выбрать услугу, дату и время\n"
        "- Помощь: это сообщение\n"
        "- /admin: панель администратора (для админа)"
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

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    
    logger.info(f"Админ {user_id} открыл панель управления")
    keyboard = [
        [InlineKeyboardButton("Посмотреть все записи", callback_data="admin_view")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Админ-панель:", reply_markup=reply_markup)

async def view_all_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещен.")
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
            callback_data = f"admin_delete_{booking_id}"
            keyboard.append([
                InlineKeyboardButton(f"{user_name}: {service} на {date} в {time}", callback_data="noop"),
                InlineKeyboardButton("Удалить", callback_data=callback_data)
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Все записи:", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при просмотре всех записей: {type(e).__name__}: {str(e)}")
        await query.edit_message_text("Ошибка при загрузке записей.")

async def delete_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещен.")
        return
    
    booking_id = int(query.data.split("_")[2])
    logger.info(f"Админ {user_id} пытается удалить запись {booking_id}")
    
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
        logger.info(f"Извлечены данные записи: {user_name}, {service}, {date}, {time}")
        
        cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()
        
        keyboard = [[InlineKeyboardButton("Назад в админ-панель", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Запись удалена: {user_name} - {service} на {date} в {time}",
            reply_markup=reply_markup
        )
        logger.info(f"Админ {user_id} удалил запись {booking_id}")
    except Exception as e:
        logger.error(f"Ошибка при удалении записи: {type(e).__name__}: {str(e)}")
        await query.edit_message_text("Ошибка при удалении записи.")

async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещен.")
        return
    
    logger.info(f"Админ {user_id} вернулся в панель управления")
    keyboard = [
        [InlineKeyboardButton("Посмотреть все записи", callback_data="admin_view")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Админ-панель:", reply_markup=reply_markup)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("Доступ запрещен.")
        return
    
    logger.info(f"Админ {user_id} запросил статистику через админ-панель")
    await send_stats(query, context)

def main():
    init_db()
    
    application = Application.builder().token(TOKEN).job_queue(JobQueue()).build()
    application.job_queue.start()
    
    conv_handler = ConversationHandler(
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
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(MessageHandler(filters.Regex(r"^(Помощь)$"), help_command))
    application.add_handler(MessageHandler(filters.Regex(r"^(Посмотреть записи)$"), view_bookings))
    application.add_handler(CallbackQueryHandler(pay, pattern="^pay$"))
    application.add_handler(CallbackQueryHandler(cancel_booking, pattern="^cancel_"))
    application.add_handler(CallbackQueryHandler(view_all_bookings, pattern="^admin_view$"))
    application.add_handler(CallbackQueryHandler(delete_booking, pattern="^admin_delete_"))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(back_to_admin, pattern="^back_to_admin$"))
    
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
