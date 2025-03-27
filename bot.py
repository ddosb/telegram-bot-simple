import logging
import asyncio
import nest_asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ConversationHandler
import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials
import os
import json

# Фикс для event loop
nest_asyncio.apply()

# Токен и ID из переменных окружения
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для разговора
SERVICE, DATE = range(2)

# Подключение к Google Sheets
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread_asyncio.AsyncioGspreadClientManager(lambda: creds)

# Асинхронная функция записи
async def write_booking(user_name, service, date):
    try:
        client = get_gspread_client()
        agc = await client.authorize()
        sheet = await agc.open_by_key("1tDnIzjnvKRyE31fMxL3qJuWG4T8tTf3MnU-38URY1_4")
        worksheet = await sheet.get_worksheet(0)
        await worksheet.append_row([user_name, service, date])
        logger.info(f"Запись добавлена: {user_name}, {service}, {date}")
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {type(e).__name__}: {str(e)}")

# Асинхронная функция просмотра записей
async def list_bookings(update: Update, context: CallbackContext):
    try:
        client = get_gspread_client()
        agc = await client.authorize()
        sheet = await agc.open_by_key("1tDnIzjnvKRyE31fMxL3qJuWG4T8tTf3MnU-38URY1_4")
        worksheet = await sheet.get_worksheet(0)
        records = await worksheet.get_all_values()
        if len(records) <= 1:
            await update.message.reply_text("Записей пока нет.")
        else:
            response = "📋 Список записей:\n"
            for row in records[1:]:
                response += f"Имя: {row[0]}, Услуга: {row[1]}, Дата: {row[2]}\n"
            await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка чтения Google Sheets: {type(e).__name__}: {str(e)}")
        await update.message.reply_text("Ошибка при загрузке записей.")

# Админ-команда /stats
async def stats(update: Update, context: CallbackContext):
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

# Начало разговора
async def start(update: Update, context: CallbackContext):
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я бот для записи на услуги.", reply_markup=reply_markup)
    return ConversationHandler.END

# Обработка кнопки "Записаться"
async def book_start(update: Update, context: CallbackContext):
    await update.message.reply_text("Введи название услуги (например, Массаж):", reply_markup=ReplyKeyboardRemove())
    return SERVICE

# Получение услуги
async def get_service(update: Update, context: CallbackContext):
    context.user_data["service"] = update.message.text
    await update.message.reply_text("Введи дату (например, 12.04):")
    return DATE

# Получение даты и запись
async def get_date(update: Update, context: CallbackContext):
    user = update.message.from_user
    service = context.user_data["service"]
    date = update.message.text
    await write_booking(user.full_name, service, date)
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"✅ Запись оформлена!\n🗓 Услуга: {service}\n📅 Дата: {date}", reply_markup=reply_markup)
    return ConversationHandler.END

# Помощь
async def help_command(update: Update, context: CallbackContext):
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Я помогу записаться на услуги. Нажми 'Записаться' и следуй инструкциям!", reply_markup=reply_markup)
    return ConversationHandler.END

# Отмена
async def cancel(update: Update, context: CallbackContext):
    keyboard = [["Записаться", "Посмотреть записи"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Запись отменена.", reply_markup=reply_markup)
    return ConversationHandler.END

async def main():
    app = Application.builder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^(Записаться)$"), book_start),
            MessageHandler(filters.Regex("^(Посмотреть записи)$"), list_bookings),
            MessageHandler(filters.Regex("^(Помощь)$"), help_command),
        ],
        states={
            SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("stats", stats))
    print("Бот запущен...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())