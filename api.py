# api.py - FastAPI Backend, API, Static Files, и Webhook Handler

import os
import sys
import logging
from datetime import datetime, timezone
import json
from base64 import b64decode

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Telegram Bot API (python-telegram-bot)
from telegram import Update
from telegram.ext import Application
from bot import get_telegram_application # Импортируем настроенное приложение из bot.py

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, auth, exceptions
from google.cloud.firestore_v1.transaction import Transaction

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
app = FastAPI()
db: firestore.client = None
telegram_application: Application = None

# --- КОНСТАНТЫ И КОНФИГУРАЦИЯ ---
BASE_URL = os.getenv("BASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Используем FIREBASE_KEY, который содержит Base64-строку из FIREBASE_SERVICE_ACCOUNT_KEY
FIREBASE_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY") 
APP_ID = os.getenv("APP_ID", "tashboss-mini-app")

if not BASE_URL:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная BASE_URL не установлена.")
    sys.exit(1)
if not BOT_TOKEN:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная BOT_TOKEN не установлена.")
    sys.exit(1)
if not FIREBASE_KEY:
    logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная FIREBASE_SERVICE_ACCOUNT_KEY не установлена.")
    sys.exit(1)

# Таблица стоимости и дохода секторов (в секунду)
SECTOR_CONFIG = {
    "sector1": {"cost": 100, "income_per_second": 0.5}, # Зона отдыха
    "sector2": {"cost": 500, "income_per_second": 2.0}, # Бизнес-центр
    "sector3": {"cost": 2500, "income_per_second": 10.0}, # Индустриальная зона
}

# --- MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем ВСЕ источники, это необходимо для Telegram WebApp
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ИНИЦИАЛИЗАЦИЯ (Запускается один раз при старте сервера) ---

@app.on_event("startup")
async def startup_event():
    """Инициализация Firebase и Telegram Application."""
    global db, telegram_application
    
    # 1. Инициализация Firebase
    try:
        # --- БЕЗОПАСНАЯ ОБРАБОТКА BASE64 КЛЮЧА ---
        # Рассчитываем и добавляем необходимое количество символов '=' для Base64 padding
        padding_needed = -len(FIREBASE_KEY) % 4
        padded_key = FIREBASE_KEY + '=' * padding_needed
        
        # Декодируем ключ
        decoded_key_bytes = b64decode(padded_key)
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        # --- КОНЕЦ БЕЗОПАСНОЙ ОБРАБОТКИ ---
        
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("✅ Firebase успешно инициализирован.")
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать Firebase. Ошибка: {e}")
        sys.exit(1)

    # 2. Инициализация Telegram Application (используя код из bot.py)
    webhook_url = f"{BASE_URL}/webhook"
    try:
        telegram_application = get_telegram_application(BOT_TOKEN, BASE_URL)
        logger.info("✅ Telegram Application успешно инициализирован.")
        
        # Установка Webhook. Используем force_set=True для гарантированной установки.
        await telegram_application.bot.set_webhook(url=webhook_url, allowed_updates=["message"])
        
        # Дополнительная проверка: Получаем информацию о вебхуке для подтверждения
        webhook_info = await telegram_application.bot.get_webhook_info()
        
        if webhook_info.url == webhook_url:
            logger.info(f"✅ Webhook успешно установлен и подтвержден на: {webhook_info.url}")
        else:
            logger.error(f"⚠️ Webhook установлен, но URL не совпадает. Ожидался: {webhook_url}, Получен: {webhook_info.url}")

    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать Telegram Application или установить Webhook: {e}")
        sys.exit(1)

# --- АУТЕНТИФИКАЦИЯ ---

async def get_auth_data(request: Request) -> str:
    """Извлекает и верифицирует токен Firebase, возвращает UID пользователя."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication failed: Missing or invalid Authorization header.")

    token = auth_header.split(" ")[1]
    
    try:
        # Проверяем токен Firebase (выданный Telegram WebApp)
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        return uid
    except exceptions.FirebaseError as e:
        logger.error(f"Firebase token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed: Invalid token.")
    except Exception as e:
        logger.error(f"General authentication error: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed.")

# --- FIREBASE HELPERS ---

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db.collection(f"artifacts/{APP_ID}/users/{user_id}/tashboss_clicker").document(user_id)

def get_initial_state():
    """Возвращает начальное состояние игры."""
    return {
        "balance": 100.0,
        "sectors": {
            "sector1": 0,
            "sector2": 0,
            "sector3": 0,
        },
        "last_collection_time": datetime.now(timezone.utc),
    }

# --- API ЭНДПОИНТЫ ---

@app.post("/api/load_state")
@firestore.transactional
async def load_state(transaction: Transaction, user_id: str = Depends(get_auth_data)):
    """
    Загружает или инициализирует состояние игры пользователя.
    Использует транзакцию для атомарности.
    """
    doc_ref = get_user_doc_ref(user_id)
    doc_snapshot = doc_ref.get(transaction=transaction)

    if doc_snapshot.exists:
        state = doc_snapshot.to_dict()
        
        # Конвертируем метку времени Firestore в ISO-строку для фронтенда
        if 'last_collection_time' in state and state['last_collection_time']:
            state['last_collection_time'] = state['last_collection_time'].isoformat()
        
        return JSONResponse(content={"status": "loaded", "state": state})
    else:
        # Инициализируем и сохраняем новое состояние
        initial_state = get_initial_state()
        doc_ref.set(initial_state, transaction=transaction)
        
        # Конвертируем метку времени для ответа
        initial_state['last_collection_time'] = initial_state['last_collection_time'].isoformat()

        return JSONResponse(content={"status": "initialized", "state": initial_state})

@app.post("/api/collect_income")
@firestore.transactional
async def collect_income(transaction: Transaction, user_id: str = Depends(get_auth_data)):
    """Рассчитывает пассивный доход, обновляет баланс и время сбора."""
    doc_ref = get_user_doc_ref(user_id)
    doc_snapshot = doc_ref.get(transaction=transaction)

    if not doc_snapshot.exists:
        raise HTTPException(status_code=404, detail="Game state not found. Please load state first.")

    state = doc_snapshot.to_dict()
    current_time = datetime.now(timezone.utc)
    
    # Конвертируем метку времени Firestore в объект datetime
    last_collection_time = state['last_collection_time'].replace(tzinfo=timezone.utc)

    # Разница во времени в секундах
    time_delta_seconds = (current_time - last_collection_time).total_seconds()
    
    if time_delta_seconds <= 0:
        return JSONResponse(content={"status": "ok", "income": 0.0, "new_balance": state['balance']})

    total_income = 0.0
    
    # Расчет дохода
    for sector_key, level in state['sectors'].items():
        if level > 0 and sector_key in SECTOR_CONFIG:
            income_per_second = SECTOR_CONFIG[sector_key]['income_per_second']
            total_income += level * income_per_second * time_delta_seconds

    # Обновление состояния
    state['balance'] += total_income
    state['last_collection_time'] = current_time # Обновляем время сбора

    # Сохранение обновленного состояния
    doc_ref.set(state, transaction=transaction)

    # Конвертируем метку времени для ответа
    state['last_collection_time'] = current_time.isoformat()
    
    return JSONResponse(content={"status": "ok", "income": round(total_income, 2), "new_balance": round(state['balance'], 2), "state": state})

@app.post("/api/buy_sector")
@firestore.transactional
async def buy_sector(transaction: Transaction, request: Request, user_id: str = Depends(get_auth_data)):
    """Покупает один уровень сектора, обновляет баланс и уровень сектора."""
    doc_ref = get_user_doc_ref(user_id)
    doc_snapshot = doc_ref.get(transaction=transaction)

    if not doc_snapshot.exists:
        raise HTTPException(status_code=404, detail="Game state not found. Please load state first.")
    
    try:
        data = await request.json()
        sector_key = data.get("sector_key")
    except:
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    if sector_key not in SECTOR_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid sector key.")

    state = doc_snapshot.to_dict()
    sector_info = SECTOR_CONFIG[sector_key]
    cost = sector_info['cost']

    if state['balance'] < cost:
        raise HTTPException(status_code=400, detail="Insufficient funds.")

    # Обновление состояния
    state['balance'] -= cost
    state['sectors'][sector_key] = state['sectors'].get(sector_key, 0) + 1

    # Сохранение обновленного состояния
    doc_ref.set(state, transaction=transaction)
    
    # Конвертируем метку времени для ответа
    state['last_collection_time'] = state['last_collection_time'].isoformat()

    return JSONResponse(content={
        "status": "bought", 
        "sector": sector_key, 
        "new_level": state['sectors'][sector_key], 
        "new_balance": round(state['balance'], 2),
        "state": state
    })

# --- TELEGRAM WEBHOOK HANDLER ---

@app.post("/webhook")
async def webhook(request: Request):
    """Обрабатывает входящие обновления Telegram Webhook."""
    try:
        # Извлекаем JSON тело из запроса
        update_json = await request.json()
        update = Update.de_json(update_json, telegram_application.bot)
        
        # Обрабатываем обновление с помощью Telegram Application
        await telegram_application.process_update(update)
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        # Возвращаем 200 OK, даже если произошла ошибка, чтобы Telegram не пытался повторно отправить обновление
        return {"status": "error", "message": str(e)}, 200

# --- СТАТИЧЕСКИЕ ФАЙЛЫ (Обслуживание WebApp) ---

# Монтируем StaticFiles для обслуживания index.html и app.js
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_index():
    """Обслуживает index.html для Telegram WebApp."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html not found!</h1>", status_code=500)
