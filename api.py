import os
import sys
import logging
import json
from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime, timezone
from typing import Dict, Any, Tuple

# FastAPI/Starlette imports
from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

# Telegram Bot imports
try:
    import telegram
    from telegram import Update, WebAppInfo
    from telegram.ext import Application, CommandHandler, CallbackContext
except ImportError:
    logging.critical("❌ CRITICAL ERROR: Library 'python-telegram-bot' not found. Please install it.")
    sys.exit(1)

# Firebase Admin SDK imports
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, auth, exceptions as firebase_exceptions
    from firebase_admin._firestore_helpers import transactional
except ImportError:
    logging.critical("❌ CRITICAL ERROR: Library 'firebase-admin' not found. Please install it.")
    sys.exit(1)

# Third-party HTTP client for Telegram webhook logging
import httpx

# --- Configuration and Initialization ---

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("api")

# Глобальные переменные
db: firestore.client = None
firebase_auth: auth = None
telegram_bot: telegram.Bot = None
APP_ID = "tashboss-clicker-app" # Идентификатор приложения для пути Firestore
FIREBASE_INITIALIZED = False

# Замените на фактический URL вашего сервиса (Render URL)
BASE_URL = os.environ.get("BASE_URL") # Передается из переменной окружения Render
PORT = int(os.environ.get("PORT", 8080))

# Логика игры
SECTORS_CONFIG = {
    "sector1": {"name": "Сектор A", "click_value": 1, "multiplier": 1.0},
    "sector2": {"name": "Сектор B", "click_value": 5, "multiplier": 1.5},
}

# --- Firebase Functions ---

def initialize_firebase():
    """Инициализирует Firebase Admin SDK."""
    global db, firebase_auth, FIREBASE_INITIALIZED
    
    # Пытаемся получить ключ из переменных окружения
    key_b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_B64")
    key_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    
    service_account_info = None

    if key_b64:
        # 1. Попытка декодировать из Base64
        try:
            service_account_json = b64decode(key_b64).decode('utf-8')
            service_account_info = json.loads(service_account_json)
            logger.info("Firebase key successfully decoded from Base64.")
        except (BinasciiError, json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to decode Firebase key as Base64 (BinasciiError or JSONDecodeError). Trying as raw JSON...")
    
    if not service_account_info and key_raw:
        # 2. Попытка разобрать как raw JSON (если Base64 не сработал или key_b64 не задан)
        try:
            service_account_info = json.loads(key_raw)
            logger.info("Firebase key successfully parsed as raw JSON string.")
        except json.JSONDecodeError:
            logger.error("Failed to parse Firebase key as raw JSON. Check FIREBASE_SERVICE_ACCOUNT_KEY environment variable.")
            return

    if service_account_info:
        try:
            # Инициализация приложения Firebase
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            firebase_auth = auth
            FIREBASE_INITIALIZED = True
            logger.info("✅ Firebase successfully initialized.")
        except Exception as e:
            logger.error(f"❌ Error initializing Firebase Admin SDK: {e}")
    else:
        logger.error("❌ Firebase service account key not found in environment variables.")

def get_user_doc_ref(user_id: str) -> firestore.document.DocumentReference:
    """Возвращает ссылку на документ пользователя в приватной коллекции."""
    return db.collection("artifacts").document(APP_ID).collection("users").document(user_id).collection("data").document("state")

# --- Authentication/Claim Functions ---

async def create_custom_token(user_id: str) -> Tuple[str, str | None]:
    """Создает Firebase Custom Token для аутентификации пользователя."""
    if not FIREBASE_INITIALIZED:
        return None, "Firebase is not initialized."

    try:
        # 1. Создаем пользователя, если он не существует
        try:
            user = firebase_auth.get_user(user_id)
            logger.info(f"Existing user found: {user_id}")
        except firebase_exceptions.NotFoundError:
            user = firebase_auth.create_user(uid=user_id)
            logger.info(f"New user created: {user_id}")

        # 2. Создаем Custom Token
        custom_token = firebase_auth.create_custom_token(user_id)
        # Custom token - это bytes, нужно декодировать
        return custom_token.decode('utf-8'), None
    except Exception as e:
        logger.error(f"Error creating custom token for user {user_id}: {e}")
        return None, str(e)


async def auth_token_handler(request: Request) -> JSONResponse:
    """Обрабатывает запрос на получение Custom Token для WebApp."""
    try:
        data = await request.json()
        telegram_user_id = data.get("telegram_user_id")

        if not telegram_user_id:
            return JSONResponse({"error": "Missing telegram_user_id"}, status_code=400)

        custom_token, error = await create_custom_token(str(telegram_user_id))

        if error:
            return JSONResponse({"error": f"Failed to create token: {error}"}, status_code=500)

        # Возвращаем Custom Token
        return JSONResponse({
            "token": custom_token,
            "firebaseConfig": json.dumps({"appId": APP_ID, "apiKey": "mock_api_key_for_client_side"})
        })

    except Exception as e:
        logger.error(f"Unhandled error in auth_token_handler: {e}")
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)


# --- Game Logic Functions (Click/Upgrade) ---

def get_base_data(user_id: str) -> Dict[str, Any]:
    """Получает или инициализирует данные пользователя."""
    # Все данные хранятся в одном документе "state"
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            # Убедимся, что все поля существуют
            return {
                "balance": data.get("balance", 0),
                "sector": data.get("sector", "sector1"),
                "clicks": data.get("clicks", 0),
                "last_active": data.get("last_active", datetime.now(timezone.utc).isoformat()),
                "auto_mining_rate": data.get("auto_mining_rate", 0),
            }
        else:
            # Инициализация нового пользователя
            initial_data = {
                "balance": 100, # Начальный бонус для старта
                "sector": "sector1",
                "clicks": 0,
                "last_active": datetime.now(timezone.utc).isoformat(),
                "auto_mining_rate": 0,
            }
            # Используем setDoc с merge=True для инициализации
            doc_ref.set(initial_data, merge=True) 
            logger.info(f"Initialized new user data for {user_id}")
            return initial_data
    except Exception as e:
        logger.error(f"Error fetching/initializing user data for {user_id}: {e}")
        # Возвращаем дефолтные данные в случае ошибки
        return {
            "balance": 0, 
            "sector": "sector1",
            "clicks": 0,
            "last_active": datetime.now(timezone.utc).isoformat(),
            "auto_mining_rate": 0,
        }

@firestore.transactional
def update_user_data_transaction(transaction: firestore.transaction, user_id: str, sector_key: str) -> Tuple[bool, int]:
    """Транзакционно обрабатывает клик."""
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        # 1. Чтение данных
        snapshot = doc_ref.get(transaction=transaction)
        
        if not snapshot.exists:
            # Инициализация (должна быть сделана ранее, но на всякий случай)
            initial_data = get_base_data(user_id) 
            snapshot = doc_ref.get(transaction=transaction) # Повторное чтение
        
        data = snapshot.to_dict()

        current_balance = data.get("balance", 0)
        current_clicks = data.get("clicks", 0)
        
        # 2. Расчет
        # Получаем данные сектора (должен быть передан корректный ключ)
        sector_info = SECTORS_CONFIG.get(sector_key, SECTORS_CONFIG["sector1"])
        click_reward = sector_info["click_value"]
        
        new_balance = current_balance + click_reward
        
        # 3. Запись данных
        transaction.update(doc_ref, {
            "balance": new_balance,
            "clicks": current_clicks + 1,
            "last_active": datetime.now(timezone.utc).isoformat(),
        })
        
        return True, new_balance

    except Exception as e:
        logger.error(f"Transaction failed for user {user_id}: {e}")
        # Возвращаем False и текущий баланс в случае ошибки
        return False, data.get("balance", 0)


async def click_handler(request: Request) -> JSONResponse:
    """Обрабатывает клик пользователя (увеличение баланса)."""
    if not db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)

    try:
        data = await request.json()
        user_id = data.get("user_id")
        sector_key = data.get("sector_key", "sector1")
        
        if not user_id:
            return JSONResponse({"error": "Missing user_id"}, status_code=400)
        
        # Запускаем транзакцию
        transaction = db.transaction()
        success, new_balance = update_user_data_transaction(transaction, user_id, sector_key)

        if success:
            return JSONResponse({"status": "ok", "new_balance": new_balance})
        else:
            return JSONResponse({"error": "Transaction failed"}, status_code=500)

    except Exception as e:
        logger.error(f"Unhandled error in click_handler: {e}")
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)


# --- Telegram Bot Handlers ---

async def start_command(update: Update, context: CallbackContext) -> None:
    """Обрабатывает команду /start, отправляя WebApp."""
    user = update.effective_user
    
    # URL, по которому будет запущен ваш WebApp (например, https://tashboss.onrender.com)
    # Здесь мы используем BASE_URL из переменной окружения
    webapp_url = f"{BASE_URL}/"

    # Создаем кнопку, которая откроет WebApp
    keyboard = [
        [telegram.KeyboardButton(
            "🚀 Запустить TashBoss Clicker",
            web_app=WebAppInfo(url=webapp_url) # Указываем URL WebApp
        )]
    ]
    
    # Создаем разметку для сообщения
    reply_markup = telegram.ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Отправляем приветственное сообщение
    await update.message.reply_html(
        f"Привет, {user.first_name}!\n\nДобро пожаловать в TashBoss Clicker. Нажми кнопку ниже, чтобы начать майнинг!",
        reply_markup=reply_markup
    )

async def handle_telegram_update(request: Request) -> JSONResponse:
    """Основной обработчик для входящих обновлений от Telegram."""
    if not telegram_bot:
        logger.error("Telegram bot is not initialized.")
        return JSONResponse({"error": "Telegram bot not ready"}, status_code=500)
    
    try:
        # Получаем данные обновления из запроса
        update_json = await request.json()
        update = Update.de_json(update_json, telegram_bot)
        
        # Обрабатываем обновление с помощью Application (если Application инициализирован)
        # В этой простой схеме мы будем обрабатывать вручную
        
        if update.message and update.message.text:
            text = update.message.text.strip().lower()
            if text == "/start":
                # Создаем временный контекст и вызываем обработчик команды
                context = CallbackContext(app.bot.updater.dispatcher)
                await start_command(update, context)
                
        # !!! Внимание: Если вы используете telegram.ext.Application, 
        # то нужно использовать его process_update:
        # await application.process_update(update)
        # Для простоты в Starlette/FastAPI часто проще обрабатывать обновления вручную,
        # как показано выше, или использовать httpx/aiohttp для общения с Telegram API.
        
        return JSONResponse({"status": "ok"})
    
    except Exception as e:
        logger.error(f"Error processing Telegram update: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# --- Initialization & Starlette App Setup ---

async def startup_event():
    """Событие, срабатывающее при запуске сервера."""
    logger.info("⚡️ Starting up and attempting to initialize Firebase and Telegram...")
    
    # 1. Инициализация Firebase
    initialize_firebase()

    # 2. Инициализация Telegram
    global telegram_bot
    bot_token = os.environ.get("BOT_TOKEN")
    
    if bot_token:
        try:
            telegram_bot = telegram.Bot(bot_token)
            
            # 3. Установка Webhook
            # Получаем URL нашего сервиса
            webhook_url = f"{BASE_URL}/telegram-webhook"
            
            # Используем httpx для асинхронного запроса (для логов)
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/setWebhook",
                    json={"url": webhook_url},
                    timeout=10 # Установим таймаут
                )

                if response.status_code == 200 and response.json().get("ok"):
                    logger.info(f"✅ Telegram Webhook set to: {webhook_url}")
                else:
                    error_message = response.json().get("description", "Unknown error")
                    # Логируем как ошибку, но не останавливаем приложение
                    logger.error(f"❌ ERROR setting Telegram Webhook: {error_message}")
                    
        except Exception as e:
            logger.error(f"❌ ERROR during Telegram bot initialization or webhook setup: {e}")
    else:
        logger.error("❌ BOT_TOKEN environment variable not found. Telegram bot disabled.")

    
# Настройка маршрутов
routes = [
    Route("/auth-token", endpoint=auth_token_handler, methods=["POST"]), # Маршрут для получения токена аутентификации
    Route("/click", endpoint=click_handler, methods=["POST"]),         # Маршрут для обработки кликов
    Route("/telegram-webhook", endpoint=handle_telegram_update, methods=["POST"]), # Маршрут для Telegram
    Route("/", endpoint=lambda r: FileResponse("index.html"), methods=["GET"]), # Фронтенд
]

# Настройка middleware (CORS)
middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
]

# Создание Starlette/FastAPI приложения
app = Starlette(
    routes=routes, 
    middleware=middleware, 
    on_startup=[startup_event]
)
