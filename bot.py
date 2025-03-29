import os
import logging
from dotenv import load_dotenv
import asyncio
import nest_asyncio
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
import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials
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
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")

SERVICE, DATE = range(2)

def get_gspread_client():
    credentials_dict = eval(GOOGLE_SHEETS_CREDENTIALS)
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(
        credentials_dict,
        scopes=['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    )
    return gspread_asyncio.AsyncioGspreadClientManager(lambda: credentials)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на услуги.", reply_markup=reply_markup)
    return ConversationHandler.END

async def record(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    dates = []
    for i in range(7):
        date = datetime.now() + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        dates.append(InlineKeyboardButton(date_str, callback_data=date_str))
    reply_markup = InlineKeyboardMarkup([dates])
    await query.edit_message_text(f"Выбрана услуга: {service}\nВыбери дату:", reply_markup=reply_markup)
    return DATE

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    admin_id = job.data["admin_id"]
    service = job.data["service"]
    date = job.data["date"]
    await context.bot.send_message(chat_id, f"Напоминание: завтра у вас {service} на {date}!")
    await context.bot.send_message(admin_id, f"Клиент записан на {service} завтра в {date}.")

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    date = query.data
    service = context.user_data["service"]
    user = update.effective_user
    
    try:
        client = get_gspread_client()
        agc = await client.authorize()
        sheet = await agc.open_by_key("1tDnIzjnvKRyE31fMxL3qJuWG4T8tTf3MnU-38URY1_4")
        worksheet = await sheet.get_worksheet(0)
        await worksheet.append_row([user.full_name, service, date])
        
        current_year = datetime.now().year
        date_obj = datetime.strptime(f"{date}.{current_year}", "%d.%m.%Y")
        reminder_time = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
        
        days_until = (date_obj.date() - reminder_time.date()).days
        if days_until > 0:
            reminder_time += timedelta(days=days_until - 1)
            context.job_queue.run_once(
                send_reminder,
                when=reminder_time,
                data={"chat_id": user.id, "admin_id": ADMIN_ID, "service": service, "date": date}
            )
        
        keyboard = [[InlineKeyboardButton("Записаться снова", callback_data="restart")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"✅ Запись оформлена!\n🗓 Услуга: {service}\n📅 Дата: {date}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при записи: {type(e).__name__}: {str(e)}")
        await query.edit_message_text("Ошибка при записи данных.")
    return ConversationHandler.END

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
        client = get_gspread_client()
        agc = await client.authorize()
        sheet = await agc.open_by_key("1tDnIzjnvKRyE31fMxL3qJuWG4T8tTf3MnU-38URY1_4")
        worksheet = await sheet.get_worksheet(0)
        records = await worksheet.get_all_values()
        total = len(records) - 1
        await update.message.reply_text(f"📊 Всего записей: {total}")
    except Exception as e:
        logger.error(f"Ошибка в /stats: {type(e).__name__}: {str(e)}")
        await update.message.reply_text("Ошибка при подсчете записей.")

# Новая функция для кнопки "Помощь"
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я бот для записи на услуги!\n"
        "Доступные команды:\n"
        "- Записаться: выбрать услугу и дату\n"
        "- Посмотреть записи: список твоих записей\n"
        "- Помощь: это сообщение"
    )

# Новая функция для кнопки "Посмотреть записи"
async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    try:
        client = get_gspread_client()
        agc = await client.authorize()
        sheet = await agc.open_by_key("1tDnIzjnvKRyE31fMxL3qJuWG4T8tTf3MnU-38URY1_4")
        worksheet = await sheet.get_worksheet(0)
        records = await worksheet.get_all_values()
        
        # Фильтруем записи текущего пользователя
        user_bookings = [row for row in records[1:] if row[0] == user.full_name]
        if not user_bookings:
            await update.message.reply_text("У вас нет записей.")
            return
        
        response = "Ваши записи:\n"
        for booking in user_bookings:
            response += f"- {booking[1]} на {booking[2]}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка при просмотре записей: {type(e).__name__}: {str(e)}")
        await update.message.reply_text("Ошибка при загрузке записей.")

def main():
    application = Application.builder().token(TOKEN).job_queue(JobQueue()).build()
    application.job_queue.start()
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(Записаться)$"), record)],
        states={
            SERVICE: [CallbackQueryHandler(service)],
            DATE: [CallbackQueryHandler(get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(restart, pattern="^restart$"))
    application.add_handler(MessageHandler(filters.Regex("^(Помощь)$"), help_command))  # Обработчик для "Помощь"
    application.add_handler(MessageHandler(filters.Regex("^(Посмотреть записи)$"), view_bookings))  # Обработчик для "Посмотреть записи")
    
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
