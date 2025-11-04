import os
import sys
import json
import logging
from datetime import datetime, timedelta
import asyncio 
import random 
from typing import Dict

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

# Импорты для Firebase/Firestore
import firebase_admin
from firebase_admin import credentials, firestore
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, error as telegram_error 
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# КРИТИЧЕСКИ ВАЖНО: Инициализация 'app' на верхнем уровне для Gunicorn
app = FastAPI(title="TashBoss Clicker API", description="Backend for Telegram Mini App")
# -------------------------------------------------------------


# Настройка CORS
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Authorization"],
)

# --- КОНФИГУРАЦИЯ ---
FIREBASE_KEY_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
TOKEN = os.getenv("BOT_TOKEN") 
BASE_URL = os.getenv("BASE_URL") or "https://tashboss.onrender.com"
WEB_APP_URL = f"{BASE_URL}" 

# --------------------

# Инициализация Firebase Admin SDK
db = None
def initialize_firebase():
    """Инициализация Firebase Admin SDK с использованием ключа из переменной окружения."""
    global db
    
    if FIREBASE_KEY_JSON and not firebase_admin._apps:
        try:
            # ---> КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Очистка JSON-строки <---
            cleaned_json_string = FIREBASE_KEY_JSON.strip() 
            
            # Парсим JSON-строку
            cred_dict = json.loads(cleaned_json_string)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info("Firebase Admin SDK успешно инициализирован.")
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка JSONDecodeError при парсинге ключа Firebase: {e}. Проверьте форматирование ключа.")
            db = None
        except Exception as e:
            logger.error(f"Непредвиденная ошибка инициализации Firebase Admin SDK: {e}")
            db = None
    elif firebase_admin._apps:
        db = firestore.client()
        logger.info("Firebase Admin SDK уже инициализирован.")
    else:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_KEY не установлен. Firestore будет недоступен.")

initialize_firebase()

# --- СХЕМЫ ДАННЫХ (Остаются без изменений) ---
class UserState(BaseModel):
    balance: float = Field(default=0.0)
    sectors: Dict[str, int] = Field(default_factory=lambda: {"sector1": 0, "sector2": 0, "sector3": 0})
    last_collection_time: str = Field(default=datetime.now().isoformat())

class BuySectorRequest(BaseModel):
    sector: str

# --- СТАВКИ И ЗАТРАТЫ (Остаются без изменений) ---
INCOME_RATES = {
    "sector1": 0.5, 
    "sector2": 2.0, 
    "sector3": 10.0
}
SECTOR_COSTS = {
    "sector1": 100.0, 
    "sector2": 500.0, 
    "sector3": 2500.0
}
MAX_IDLE_TIME = 10 * 24 * 3600 # 10 дней в секундах

# --- ФУНКЦИИ АУТЕНТИФИКАЦИИ И FIREBASE (Исправлен только вызов get_db_ref) ---

def get_db_ref(user_id: str):
    """Получает ссылку на документ пользователя в Firestore."""
    if not db:
        # Если Firebase не инициализирован, выбрасываем 500 ошибку
        raise HTTPException(status_code=500, detail="Firestore не инициализирован. Проверьте FIREBASE_SERVICE_ACCOUNT_KEY.")
    # Использование пути 'users' как корневой коллекции
    return db.collection("users").document(user_id) 

async def get_auth_data(request: Request) -> dict:
    """Верифицирует токен Telegram Mini App из заголовка Authorization."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует или неверный заголовок авторизации."
        )

    init_data = auth_header.split(" ")[1]
    
    # Заглушка UID на основе init_data (в реальном приложении нужна полная верификация)
    import hashlib
    user_id = hashlib.sha256(init_data.encode('utf-8')).hexdigest()
    
    return {"uid": user_id}

def calculate_income(state: UserState) -> tuple[float, datetime]:
    """Рассчитывает доход с момента последнего сбора."""
    try:
        last_time = datetime.fromisoformat(state.last_collection_time)
    except ValueError:
        last_time = datetime.now()
        
    now = datetime.now()
    delta_seconds = (now - last_time).total_seconds()
    
    effective_seconds = min(delta_seconds, MAX_IDLE_TIME)

    income = 0.0
    for sector, count in state.sectors.items():
        if sector in INCOME_RATES:
            rate = INCOME_RATES[sector]
            income += rate * count * effective_seconds
            
    return income, now

async def load_or_create_state(user_id: str) -> UserState:
    """Загружает состояние пользователя из Firestore или создает новое с 5000 BSS."""
    user_ref = get_db_ref(user_id)
    doc = user_ref.get()

    if doc.exists:
        data = doc.to_dict()
        state = UserState(**data)
        logger.info(f"Загружено состояние для UID: {user_id}")
    else:
        # Добавление стартового капитала (5000 BSS)
        state = UserState(balance=5000.0) 
        await save_state(user_id, state)
        logger.info(f"Создано новое состояние со стартовым капиталом для UID: {user_id}")
        
    return state

async def save_state(user_id: str, state: UserState):
    """Сохраняет состояние пользователя в Firestore."""
    user_ref = get_db_ref(user_id)
    user_ref.set(state.model_dump())
    logger.info(f"Сохранено состояние для UID: {user_id}")

# --- WEBHOOK и API ЭНДПОИНТЫ (Без изменений) ---

async def set_telegram_webhook():
    if tg_app:
        base_url = os.getenv("BASE_URL")
        if base_url:
            webhook_url = f"{base_url}/bot_webhook"
            await asyncio.sleep(random.uniform(0.1, 1.0))
            try:
                await tg_app.bot.set_webhook(url=webhook_url)
                logger.info(f"Установлен Telegram Webhook на: {webhook_url}")
            except telegram_error.RetryAfter as e:
                logger.warning(f"Ошибка Rate Limit: {e}. Продолжаем работу.")
            except Exception as e:
                 logger.error(f"Непредвиденная ошибка при установке вебхука: {e}")
        else:
            logger.warning("BASE_URL не установлен. Webhook не установлен.")

if tg_app:
    @app.on_event("startup")
    async def startup_event():
        try:
            await tg_app.initialize()
            logger.info("Telegram Application инициализирован.")
        except Exception as e:
            logger.error(f"Ошибка при инициализации Telegram Application: {e}")
        asyncio.create_task(set_telegram_webhook())

    @app.post("/bot_webhook")
    async def telegram_webhook(request: Request):
        try:
            body = await request.json()
            update_obj = Update.de_json(data=body, bot=tg_app.bot) 
            await tg_app.process_update(update_obj) 
            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука Telegram: {e}")
            return {"status": "error", "message": str(e)}, 200

@app.post("/api/load_state")
async def load_state(request: Request):
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data.get("uid")

        state = await load_or_create_state(user_id)
        collected_income, current_time = calculate_income(state)
        
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()
        
        await save_state(user_id, state)

        return {"status": "ok", "state": state.model_dump(), "collected_income": collected_income}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в load_state: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при загрузке состояния.")


@app.post("/api/collect_income")
async def collect_income(request: Request):
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data.get("uid")

        state = await load_or_create_state(user_id)
        collected_income, current_time = calculate_income(state)
        
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()

        await save_state(user_id, state)
        
        return {"status": "ok", "state": state.model_dump(), "collected_income": collected_income}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в collect_income: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при сборе дохода.")

@app.post("/api/buy_sector")
async def buy_sector(req: BuySectorRequest, request: Request):
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data.get("uid")
        sector_name = req.sector

        if sector_name not in SECTOR_COSTS:
            raise HTTPException(status_code=400, detail="Неверное название сектора.")

        state = await load_or_create_state(user_id)
        current_count = state.sectors.get(sector_name, 0)
        
        cost = SECTOR_COSTS[sector_name] * (current_count + 1)
        
        if state.balance < cost:
            raise HTTPException(status_code=400, detail="Недостаточно средств для покупки.")
        
        collected_income, current_time = calculate_income(state)
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()

        state.balance -= cost
        state.sectors[sector_name] = state.sectors.get(sector_name, 0) + 1

        await save_state(user_id, state)

        return {"status": "ok", "state": state.model_dump()}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в buy_sector: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при покупке сектора.")

# --- ОБСЛУЖИВАНИЕ СТАТИЧЕСКИХ ФАЙЛОВ И WEBAPP (Без изменений) ---

@app.get("/health_check")
def read_root():
    return {"status": "ok", "message": "TashBoss Clicker API is running (Fixed Firebase Init)."}

app.mount("/", StaticFiles(directory=".", html=True), name="static")
