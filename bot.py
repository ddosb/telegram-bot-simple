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
)
import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials

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

# Состояния диалога
SERVICE, DATE = range(2)

# Функция авторизации Google Sheets
def get_gspread_client():
    credentials_dict = eval(GOOGLE_SHEETS_CREDENTIALS)
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(
        credentials_dict,
        scopes=['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    )
    return gspread_asyncio.AsyncioGspreadClientManager(lambda: credentials)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на услуги.", reply_markup=reply_markup)
    return ConversationHandler.END

# Начало диалога "Записаться"
async def record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Список услуг для кнопок
    services = [
        InlineKeyboardButton("Массаж", callback_data="Массаж"),
        InlineKeyboardButton("Маникюр", callback_data="Маникюр"),
        InlineKeyboardButton("Стрижка", callback_data="Стрижка"),
    ]
    reply_markup = InlineKeyboardMarkup([services])  # Одна строка кнопок
    await update.message.reply_text("Выбери услугу:", reply_markup=reply_markup)
    return SERVICE

# Обработка выбора услуги
async def service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Подтверждаем нажатие кнопки
    service = query.data  # Получаем выбранную услугу
    context.user_data["service"] = service
    
    # Генерируем кнопки с датами (следующие 7 дней)
    from datetime import datetime, timedelta
    dates = []
    for i in range(7):
        date = datetime.now() + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        dates.append(InlineKeyboardButton(date_str, callback_data=date_str))
    reply_markup = InlineKeyboardMarkup([dates])  # Одна строка кнопок
    await query.edit_message_text(f"Выбрана услуга: {service}\nВыбери дату:", reply_markup=reply_markup)
    return DATE

# Обработка выбора даты и запись
async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    date = query.data  # Получаем выбранную дату
    service = context.user_data["service"]
    user = update.effective_user
    
    try:
        # Запись в Google Sheets
        client = get_gspread_client()
        agc = await client.authorize()
        sheet = await agc.open_by_key("1tDnIzjnvKRyE31fMxL3qJuWG4T8tTf3MnU-38URY1_4")
        worksheet = await sheet.get_worksheet(0)
        await worksheet.append_row([user.full_name, service, date])
        
        # InlineKeyboard после записи
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

# Отмена диалога
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запись отменена.")
    return ConversationHandler.END

# Статистика для админа
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

def main():
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(Записаться)$"), record)],
        states={
            SERVICE: [CallbackQueryHandler(service)],
            DATE: [CallbackQueryHandler(get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                  CallbackQueryHandler(record, pattern="^restart$")],  # Добавляем обработчик
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stats", stats))
    
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
