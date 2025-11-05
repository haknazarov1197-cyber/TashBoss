# bot.py
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logger = logging.getLogger(__name__)

# --- Обработчики команд ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и кнопку для запуска Mini App."""
    if not update.message:
        return

    # BASE_URL теперь должен быть передан в application.bot_data в api.py
    base_url = context.application.bot_data.get('BASE_URL')
    
    if not base_url:
        # Этого не должно случиться, если api.py правильно настроен,
        # но это важная проверка.
        await update.message.reply_text("Ошибка: Не удалось получить базовый URL сервера. Пожалуйста, сообщите администратору.")
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
    
    application = Application.builder().token(bot_token).build()

    # Сохраняем BASE_URL в bot_data, чтобы он был доступен в start_command
    # Используем .application.bot_data, так как Application уже построен
    application.bot_data['BASE_URL'] = base_url

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    
    return application

# NOTE: main() для локального polling режима удалена,
# так как это приложение предназначено для вебхука на Render.
