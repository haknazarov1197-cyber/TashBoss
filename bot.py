# bot.py
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Обработчики команд ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и кнопку для запуска Mini App.
    BASE_URL передается через функцию get_telegram_application из api.py."""
    if not update.message:
        return

    # BASE_URL должен быть сохранен в контексте (context.bot_data), 
    # когда приложение инициализируется в api.py
    base_url = context.bot_data.get('BASE_URL')
    
    if not base_url:
        await update.message.reply_text("Ошибка: Не удалось получить базовый URL сервера.")
        return

    # URL для запуска Mini App. Он должен указывать на корневой путь вашего бэкенда.
    webapp_url = base_url
    
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

def get_telegram_application(bot_token: str, base_url: str) -> Application:
    """Возвращает настроенный объект Application для использования в режиме вебхука.
    Принимает токен и base_url как аргументы."""
    
    if bot_token == "YOUR_FALLBACK_TOKEN" or not bot_token:
        logger.error("BOT_TOKEN не установлен. Используется заглушка.")
        
    application = Application.builder().token(bot_token).build()

    # Сохраняем BASE_URL в bot_data, чтобы он был доступен в start_command
    application.bot_data['BASE_URL'] = base_url

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    
    # Мы не запускаем run_polling() здесь, так как сервер FastAPI будет обрабатывать вебхуки.
    return application

# Заглушка для локального тестирования (не используется на Render)
def main() -> None:
    # Здесь используется заглушка, так как os.environ не работает вне FastAPI
    application = get_telegram_application("YOUR_FALLBACK_TOKEN", "http://127.0.0.1:8000")
    print("Запуск бота в режиме polling (только для локального теста)...")
    application.run_polling(poll_interval=1.0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
