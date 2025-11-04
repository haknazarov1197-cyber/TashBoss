import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
# ВАЖНО: Токен теперь должен браться только из переменной окружения.
TOKEN = os.getenv("BOT_TOKEN") 

# ВАЖНО: URL на публичный адрес вашего развернутого FastAPI-сервера
# Render должен установить BASE_URL в env. Если нет, используется заглушка.
BASE_URL = os.getenv("BASE_URL") or "https://tashboss.onrender.com"

# Полный URL для Web App (должен совпадать с эндпоинтом в api.py)
WEB_APP_URL = f"{BASE_URL}" # Ссылаемся на корень, где смонтированы статические файлы

# --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет сообщение с кнопкой для открытия Telegram Mini App.
    """
    user = update.effective_user
    
    # Кнопка, открывающая Web App. web_app=WebAppInfo(url=...)
    # URL должен быть полным адресом к вашей index.html
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


def get_telegram_application() -> Application:
    """
    Создает и настраивает экземпляр Telegram Application.
    Эта функция будет вызвана в api.py для интеграции с Webhook.
    """
    if not TOKEN:
        logger.error("ОШИБКА: Токен бота (BOT_TOKEN) не установлен. Приложение Telegram не инициализировано.")
        # Возвращаем заглушку, чтобы избежать краха api.py
        return None

    logger.info("Инициализация Telegram Application...")
    app = Application.builder().token(TOKEN).build()

    # Обработчик команды /start
    app.add_handler(CommandHandler("start", start))

    return app
