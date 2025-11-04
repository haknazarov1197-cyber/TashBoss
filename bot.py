import os
import logging
from telegram import Update, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# NOTE: Для реального деплоя используйте переменные окружения!
# Ваш токен бота
TOKEN = os.getenv("BOT_TOKEN") or "СЮДА_ВСТАВЬ_СВОЙ_ТОКЕН"
# URL вашего Mini App. На Render это будет URL вашего API/веб-сервера.
# Пример: https://tashboss-mini-app.onrender.com
WEB_APP_URL = os.getenv("WEB_APP_URL") or "http://localhost:8000/webapp"

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение и кнопку для открытия Web App."""
    user = update.effective_user
    
    # Создаем кнопку, которая откроет Web App
    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 Открыть TashBoss Mini App",
                web_app=WebAppInfo(url=WEB_APP_URL)
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 Добро пожаловать, {user.first_name}!\n\n"
        f"Нажмите кнопку ниже, чтобы запустить игру *TashBoss* в Telegram Mini App. "
        f"Ваш прогресс будет сохранен!",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


def main():
    """Запуск бота."""
    try:
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        logger.info("Bot started polling...")
        app.run_polling(poll_interval=1.0)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")


if __name__ == "__main__":
    main()
