import os
import sys
import json
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

# Импорты для Firebase/Firestore
import firebase_admin
from firebase_admin import credentials, firestore, auth
from telegram import Update 

# Настройка логирования
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
FIREBASE_KEY_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
if not FIREBASE_KEY_JSON:
    logger.error("FIREBASE_SERVICE_ACCOUNT_KEY не установлен. Firebase Admin не инициализирован.")
    
from bot import get_telegram_application

# Инициализация Firebase Admin SDK
db = None
def initialize_firebase():
    global db
    if FIREBASE_KEY_JSON and not firebase_admin._apps:
        try:
            cred_dict = json.loads(FIREBASE_KEY_JSON)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info("Firebase Admin SDK успешно инициализирован.")
        except Exception as e:
            logger.error(f"Ошибка инициализации Firebase Admin SDK: {e}")
            db = None
    elif firebase_admin._apps:
        db = firestore.client()
        logger.info("Firebase Admin SDK уже инициализирован.")
    else:
        logger.error("Firebase не инициализирован из-за отсутствия ключа.")

initialize_firebase()

# --- СХЕМЫ ДАННЫХ ---
class UserState(BaseModel):
    balance: float = Field(default=0.0)
    sectors: dict = Field(default_factory=lambda: {"sector1": 0, "sector2": 0, "sector3": 0})
    last_collection_time: str = Field(default=datetime.now().isoformat())

class BuySectorRequest(BaseModel):
    sector: str

# --- СТАВКИ И ЗАТРАТЫ ---
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
MAX_IDLE_TIME = 10 * 24 * 3600

# --- ФУНКЦИИ АУТЕНТИФИКАЦИИ И УТИЛИТЫ ---

def get_db_ref(user_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Firestore не инициализирован.")
    return db.collection("users").document(user_id)

async def get_auth_data(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует или неверный заголовок авторизации."
        )

    init_data = auth_header.split(" ")[1]
    
    if init_data == "debug_token_123":
        logger.warning("Используется заглушка токена 'debug_token_123'.")
        return {"uid": "debug_user_id"} 

    import hashlib
    user_id = hashlib.sha256(init_data.encode('utf-8')).hexdigest()
    
    return {"uid": user_id}

def calculate_income(state: UserState) -> tuple[float, datetime]:
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
    user_ref = get_db_ref(user_id)
    doc = user_ref.get()

    if doc.exists:
        data = doc.to_dict()
        state = UserState(**data)
        logger.info(f"Загружено состояние для UID: {user_id}")
    else:
        state = UserState()
        await save_state(user_id, state)
        logger.info(f"Создано новое состояние для UID: {user_id}")
        
    return state

async def save_state(user_id: str, state: UserState):
    user_ref = get_db_ref(user_id)
    user_ref.set(state.model_dump())
    logger.info(f"Сохранено состояние для UID: {user_id}")


# --- ИНИЦИАЛИЗАЦИЯ FASTAPI И MIDDLEWARE ---
app = FastAPI(title="TashBoss Clicker API", description="Backend for Telegram Mini App")

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

tg_app = get_telegram_application()
if tg_app:
    @app.on_event("startup")
    async def startup_event():
        base_url = os.getenv("BASE_URL")
        if base_url:
            webhook_url = f"{base_url}/bot_webhook"
            await tg_app.bot.set_webhook(url=webhook_url)
            logger.info(f"Установлен Telegram Webhook на: {webhook_url}")
        else:
            logger.warning("BASE_URL не установлен. Webhook не установлен.")

    @app.post("/bot_webhook")
    async def telegram_webhook(request: Request):
        try:
            body = await request.json()
            update_obj = Update.de_json(data=body, bot=tg_app.bot) 
            await tg_app.update_queue.put(update_obj)
            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука Telegram: {e}")
            return {"status": "error", "message": str(e)}, 500


# --- API ЭНДПОИНТЫ ДЛЯ ИГРЫ ---

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

        cost = SECTOR_COSTS[sector_name]
        
        state = await load_or_create_state(user_id)

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

# --- ОБСЛУЖИВАНИЕ СТАТИЧЕСКИХ ФАЙЛОВ И WEBAPP ---

@app.get("/")
def read_root():
    return {"status": "ok", "message": "TashBoss Clicker API is running."}

# Обслуживание статических файлов (index.html, app.js, style.css)
app.mount("/", StaticFiles(directory=".", html=True), name="static") 
