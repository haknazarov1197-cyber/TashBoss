import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

# --- КОНФИГУРАЦИЯ ---
# ВАЖНО: Замените "СЮДА_ВСТАВЬ_СВОЙ_ТОКЕН" на ваш токен бота.
TOKEN = os.getenv("BOT_TOKEN") or "СЮДА_ВСТАВЬ_СВОЙ_ТОКЕН" 

# ВАЖНО: Замените URL на публичный адрес вашего развернутого FastAPI-сервера
# Пример: https://tashboss-mini-app.onrender.com
BASE_URL = os.getenv("BASE_URL") or "https://ВАШ-ПУБЛИЧНЫЙ-ДОМЕН"

# Полный URL для Web App (должен совпадать с эндпоинтом в api.py)
WEB_APP_URL = f"{BASE_URL}/webapp"

# --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет сообщение с кнопкой для открытия Telegram Mini App.
    """
    user = update.effective_user
    
    # Кнопка, открывающая Web App
    keyboard = [
        [InlineKeyboardButton("🏙 Открыть TashBoss", web_app=WebAppInfo(url=WEB_APP_URL))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Приветственное сообщение
    await update.message.reply_text(
        f"👋 Добро пожаловать, *{user.first_name}*!\n\n"
        f"Управляйте городом и зарабатывайте BossCoin (BSS) в нашем Mini App 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


def main():
    """Запускает бота."""
    try:
        if not TOKEN or TOKEN == "СЮДА_ВСТАВЬ_СВОЙ_ТОКЕН":
            print("ОШИБКА: Пожалуйста, замените 'СЮДА_ВСТАВЬ_СВОЙ_ТОКЕН' на реальный токен вашего бота.")
            return

        print("Запуск бота...")
        app = Application.builder().token(TOKEN).build()

        # Обработчик команды /start
        app.add_handler(CommandHandler("start", start))

        # Запуск polling (для локальной разработки)
        # На продакшене лучше использовать Webhooks
        app.run_polling(poll_interval=1)
        
    except Exception as e:
        print(f"Критическая ошибка при запуске бота: {e}")

if __name__ == "__main__":
    main()
