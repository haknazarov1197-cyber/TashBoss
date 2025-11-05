import os
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_FALLBACK_TOKEN")
BASE_URL = os.environ.get("BASE_URL", "https://tashboss.onrender.com")

# --- Обработчики команд ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и кнопку для запуска Mini App."""
    if not update.message:
        return

    # URL для запуска Mini App. Он должен указывать на корневой путь вашего бэкенда.
    webapp_url = BASE_URL
    
    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 Запустить TashBoss Clicker",
                web_app=WebAppInfo(url=webapp_url)
            )
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Добро пожаловать в TashBoss Clicker! Управляйте городом и зарабатывайте BossCoin.",
        reply_markup=reply_markup,
    )

# --- Функция для получения объекта Application ---

def get_telegram_application() -> Application:
    """Возвращает настроенный объект Application для использования в режиме вебхука."""
    if BOT_TOKEN == "YOUR_FALLBACK_TOKEN":
        logger.error("BOT_TOKEN не установлен в переменных окружения. Используется заглушка.")
        
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    
    # Мы не запускаем run_polling() здесь, так как сервер FastAPI будет обрабатывать вебхуки.
    return application

# Заглушка для локального тестирования (не используется на Render)
def main() -> None:
    application = get_telegram_application()
    print("Запуск бота в режиме polling (только для локального теста)...")
    application.run_polling(poll_interval=1.0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
