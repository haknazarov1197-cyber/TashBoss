import os
import sys
import logging
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from telegram.ext import Application
import firebase_admin
from firebase_admin import credentials, firestore, auth, exceptions

# Установка логирования
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# --- КОНФИГУРАЦИЯ ---
# Критически важно: эти переменные должны быть установлены на Render!
BOT_TOKEN = os.getenv('BOT_TOKEN')
BASE_URL = os.getenv('BASE_URL')
FIREBASE_KEY = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY')
APP_ID = "tashboss-clicker-webapp" # Идентификатор для пути Firestore

if not BOT_TOKEN:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения 'BOT_TOKEN' не найдена.")
    # Принудительное завершение, так как бот не может работать без токена
    sys.exit(1)

if not BASE_URL:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения 'BASE_URL' не найдена.")
    sys.exit(1)

if not FIREBASE_KEY:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения 'FIREBASE_SERVICE_ACCOUNT_KEY' не найдена.")
    sys.exit(1)

# Инициализация Firebase
try:
    # Загрузка ключа из JSON-строки, переданной в переменной окружения
    cred = credentials.Certificate(dict(eval(FIREBASE_KEY)))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    logger.info("✅ Firebase успешно инициализирован.")
except Exception as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации Firebase: {e}")
    sys.exit(1)

# --- ИНИЦИАЛИЗАЦИЯ TELEGRAM BOT ---
# Функция для создания и настройки объекта Telegram Application
def get_telegram_application() -> Application:
    """Создает и настраивает объект Telegram Application для Webhook."""
    from bot import start_command_handler
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавление обработчика для команды /start
    application.add_handler(start_command_handler)

    return application

# Создание экземпляра FastAPI
app = FastAPI(title="TashBoss API")

# --- MIDDLEWARE ---
# Настройка CORS для разрешения запросов из Telegram WebApp
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем все источники для Mini App
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация Telegram Application
try:
    application = get_telegram_application()
    logger.info("✅ Telegram Application успешно инициализирован.")
except Exception as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать Telegram Application: {e}")
    sys.exit(1)

# --- АУТЕНТИФИКАЦИЯ (ПОЛУЧЕНИЕ UID) ---
async def get_auth_data(request: Request) -> str:
    """Извлекает и верифицирует токен Telegram, возвращает UID пользователя."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("🚫 Запрос без действительного заголовка 'Authorization'.")
        raise HTTPException(status_code=401, detail="Необходима аутентификация (Bearer token)")

    id_token = auth_header.split("Bearer ")[1]
    
    try:
        # Верификация токена через Firebase Admin SDK
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token.get('uid')
        # В случае, если 'uid' не в токене (что маловероятно для кастомных токенов, но возможно), 
        # используем 'sub' как запасной вариант.
        if not uid:
             uid = decoded_token.get('sub')
        
        if not uid:
            logger.error(f"🚫 Токен верифицирован, но UID не найден: {decoded_token}")
            raise HTTPException(status_code=401, detail="UID пользователя не найден в токене.")

        return uid
    except exceptions.InvalidIdToken as e:
        logger.error(f"🚫 Недействительный токен: {e}")
        raise HTTPException(status_code=401, detail="Недействительный токен аутентификации.")
    except Exception as e:
        logger.error(f"🚫 Общая ошибка аутентификации: {e}")
        raise HTTPException(status_code=401, detail="Ошибка аутентификации.")

# --- КОНЕЧНЫЕ ТОЧКИ TELEGRAM WEBHOOK ---

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Обрабатывает входящие обновления от Telegram."""
    try:
        # Получение данных обновления из POST-запроса
        update_json = await request.json()
        
        # Обработка обновления через объект application
        await application.update_queue.put(update_json)
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка обработки Webhook: {e}")
        # Возвращаем 200, чтобы Telegram не переотправлял обновление
        return {"status": "error", "message": str(e)}

# --- КОНЕЧНЫЕ ТОЧКИ API (Логика игры) ---
# ... (Остальная логика игры: load_state, collect_income, buy_sector)

@app.get("/api/ping")
async def ping_api():
    """Простая конечная точка для проверки работоспособности API."""
    return {"status": "ok", "message": "API работает, Firebase инициализирован."}

# Инициализация API для обслуживания статических файлов
app.mount("/", StaticFiles(directory=".", html=True), name="static")
