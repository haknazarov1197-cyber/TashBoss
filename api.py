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
    from telegram import WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
except ImportError:
    logging.critical("❌ CRITICAL ERROR: Library 'python-telegram-bot' not found. Please install it.")
    sys.exit(1)

# Firebase Admin SDK imports
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, auth, exceptions as firebase_exceptions
    from firebase_admin._firestore_helpers import transactional
except ImportError:
    # Этот блок будет пропущен после установки requirements.txt
    logging.critical("❌ CRITICAL ERROR: Library 'firebase-admin' not found. Please install it.")
    sys.exit(1)

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

# Получение переменных окружения
BASE_URL = os.environ.get("BASE_URL")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Логика игры
# Конфигурация секторов (должна совпадать с фронтендом)
SECTORS_CONFIG = {
    "sector1": {"name": "Сектор A", "click_value": 1, "multiplier": 1.0},
    "sector2": {"name": "Сектор B", "click_value": 5, "multiplier": 1.5},
}

# --- Firebase Functions ---

def initialize_firebase():
    """Инициализирует Firebase Admin SDK."""
    global db, firebase_auth, FIREBASE_INITIALIZED
    
    if FIREBASE_INITIALIZED:
        return

    # Предполагаем, что ключ Firebase находится в переменной окружения FIREBASE_SERVICE_ACCOUNT_KEY
    # либо в B64-кодированном, либо в чистом JSON-формате.
    key_b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_B64")
    key_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    service_account_info = None

    if key_b64:
        try:
            service_account_json = b64decode(key_b64).decode('utf-8')
            service_account_info = json.loads(service_account_json)
        except (BinasciiError, json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to decode Firebase key from B64. Trying raw JSON.")
            pass
    
    if not service_account_info and key_raw:
        try:
            service_account_info = json.loads(key_raw)
        except json.JSONDecodeError:
            logger.error("Failed to parse Firebase key from raw JSON. Check environment variable.")
            
    if not service_account_info:
         logger.critical("❌ Firebase service account key not found in env variables or invalid.")
         return

    try:
        cred = credentials.Certificate(service_account_info)
        # Инициализация с уникальным именем приложения
        firebase_admin.initialize_app(cred, name=APP_ID)
        db = firestore.client()
        firebase_auth = auth
        FIREBASE_INITIALIZED = True
        logger.info("✅ Firebase successfully initialized.")
    except Exception as e:
        logger.error(f"❌ Error initializing Firebase Admin SDK: {e}")

def get_user_doc_ref(user_id: str) -> firestore.document.DocumentReference:
    """Возвращает ссылку на документ пользователя в приватной коллекции."""
    # Путь: /artifacts/{appId}/users/{userId}/data/state
    return db.collection("artifacts").document(APP_ID).collection("users").document(user_id).collection("data").document("state")

# --- Authentication/Claim Functions ---

async def create_custom_token(user_id: str) -> Tuple[str | None, str | None]:
    """Создает Firebase Custom Token для аутентификации пользователя."""
    if not FIREBASE_INITIALIZED:
        return None, "Firebase is not initialized."

    try:
        # Создаем пользователя, если он не существует
        try:
            firebase_auth.get_user(user_id)
        except firebase_exceptions.NotFoundError:
            firebase_auth.create_user(uid=user_id)

        # Создаем Custom Token
        custom_token = firebase_auth.create_custom_token(user_id)
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

        # Важно: Firebase User ID должен быть строкой.
        custom_token, error = await create_custom_token(str(telegram_user_id))

        if error:
            return JSONResponse({"error": f"Failed to create token: {error}"}, status_code=500)

        return JSONResponse({
            "token": custom_token,
        })

    except Exception as e:
        logger.error(f"Unhandled error in auth_token_handler: {e}")
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)

# --- Game Logic Functions (Click/Upgrade) ---

def get_base_data(user_id: str) -> Dict[str, Any]:
    """Получает или инициализирует данные пользователя, возвращая базовые значения при ошибке."""
    if not db:
        return {"balance": 0, "sector": "sector1", "clicks": 0, "last_active": datetime.now(timezone.utc).isoformat(), "auto_mining_rate": 0}
        
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            return {
                "balance": data.get("balance", 0),
                "sector": data.get("sector", "sector1"),
                "clicks": data.get("clicks", 0),
                "last_active": data.get("last_active", datetime.now(timezone.utc).isoformat()),
                "auto_mining_rate": data.get("auto_mining_rate", 0),
            }
        else:
            initial_data = {
                "balance": 100, 
                "sector": "sector1",
                "clicks": 0,
                "last_active": datetime.now(timezone.utc).isoformat(),
                "auto_mining_rate": 0,
            }
            # Устанавливаем начальные данные (вне транзакции)
            doc_ref.set(initial_data, merge=True) 
            return initial_data
    except Exception as e:
        logger.error(f"Error fetching/initializing user data for {user_id}: {e}")
        return {"balance": 0, "sector": "sector1", "clicks": 0, "last_active": datetime.now(timezone.utc).isoformat(), "auto_mining_rate": 0}

@firestore.transactional
def update_user_data_transaction(transaction: firestore.transaction, user_id: str, sector_key: str) -> Tuple[bool, int]:
    """Транзакционно обрабатывает клик."""
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        snapshot = doc_ref.get(transaction=transaction)
        
        data = snapshot.to_dict() or {}

        # Инициализация данных, если документ не существует в этой транзакции
        if not snapshot.exists:
            data = {
                "balance": 100, 
                "sector": "sector1",
                "clicks": 0,
                "last_active": datetime.now(timezone.utc).isoformat(),
                "auto_mining_rate": 0,
            }
            transaction.set(doc_ref, data) # Устанавливаем начальные данные
        
        current_balance = data.get("balance", 0)
        current_clicks = data.get("clicks", 0)
        
        sector_info = SECTORS_CONFIG.get(sector_key, SECTORS_CONFIG["sector1"])
        click_reward = sector_info["click_value"]
        
        new_balance = current_balance + click_reward
        
        transaction.update(doc_ref, {
            "balance": new_balance,
            "clicks": current_clicks + 1,
            "last_active": datetime.now(timezone.utc).isoformat(),
        })
        
        return True, new_balance

    except Exception as e:
        logger.error(f"Transaction failed for user {user_id}: {e}")
        # Возвращаем False и текущий баланс
        current_data = get_base_data(user_id)
        return False, current_data.get("balance", 0)


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

async def handle_telegram_update(request: Request) -> JSONResponse:
    """Обрабатывает входящие обновления от Telegram (WebHook)."""
    if not telegram_bot:
        logger.error("Telegram bot is not initialized.")
        return JSONResponse({"error": "Telegram bot not ready"}, status_code=500)
    
    try:
        update_json = await request.json()
        
        if 'message' in update_json:
            message = update_json['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()
            
            # Обрабатываем команду /start
            if text == "/start":
                # URL для WebApp
                webapp_url = f"{BASE_URL.rstrip('/')}/" 

                # Создаем кнопку WebApp
                keyboard = [
                    [KeyboardButton(
                        "🚀 Запустить TashBoss Clicker",
                        web_app=WebAppInfo(url=webapp_url)
                    )]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

                await telegram_bot.send_message(
                    chat_id=chat_id,
                    text="Привет! Нажми кнопку ниже, чтобы начать майнинг.",
                    reply_markup=reply_markup
                )
                logger.info(f"Sent /start response to chat_id: {chat_id}")
                return JSONResponse({"status": "ok"})
                
        
        return JSONResponse({"status": "ok"})
    
    except Exception as e:
        logger.error(f"Error processing Telegram update: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# --- Initialization & Starlette App Setup ---

async def startup_event():
    """Событие, срабатывающее при запуске сервера."""
    logger.info("⚡️ Starting up and attempting to initialize Firebase and Telegram...")
    
    initialize_firebase()

    global telegram_bot
    
    if BOT_TOKEN and BASE_URL:
        try:
            telegram_bot = telegram.Bot(BOT_TOKEN)
            
            # 3. Установка Webhook
            webhook_url = f"{BASE_URL.rstrip('/')}/telegram-webhook"
            
            # Используем httpx для асинхронной установки вебхука
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                    json={"url": webhook_url},
                    timeout=10
                )

                if response.status_code == 200 and response.json().get("ok"):
                    logger.info(f"✅ Telegram Webhook set to: {webhook_url}")
                else:
                    error_message = response.json().get("description", "Unknown error")
                    logger.error(f"❌ ERROR setting Telegram Webhook: {error_message}. Full response: {response.text}")
                    
        except Exception as e:
            logger.error(f"❌ ERROR during Telegram bot initialization or webhook setup: {e}")
    else:
        logger.error("❌ BOT_TOKEN or BASE_URL not found. Telegram bot disabled.")

    
routes = [
    # Маршрут для отдачи HTML-файла
    Route("/", endpoint=lambda r: FileResponse("index.html", media_type="text/html"), methods=["GET"]),
    
    Route("/auth-token", endpoint=auth_token_handler, methods=["POST"]),
    Route("/click", endpoint=click_handler, methods=["POST"]),
    Route("/telegram-webhook", endpoint=handle_telegram_update, methods=["POST"]),
]

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
]

# Важно: имя Starlette app должно быть 'app', чтобы соответствовать gunicorn api:app
app = Starlette(
    routes=routes, 
    middleware=middleware, 
    on_startup=[startup_event]
)
