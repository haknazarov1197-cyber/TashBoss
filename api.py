import os
import sys
import json
import logging
from datetime import datetime, timedelta
import asyncio 
import random 

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

# Импорты для Firebase/Firestore
import firebase_admin
from firebase_admin import credentials, firestore
# Добавлен импорт error для обработки RetryAfter
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
        logger.warning("FIREBASE_SERVICE_ACCOUNT_KEY не установлен. Firestore будет недоступен.")

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
MAX_IDLE_TIME = 10 * 24 * 3600 # 10 дней в секундах

# --- ЛОГИКА ТЕЛЕГРАМ БОТА (ВСТРОЕНА ИЗ bot.py) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет сообщение с кнопкой для открытия Telegram Mini App.
    """
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🏙 Открыть TashBoss", web_app=WebAppInfo(url=WEB_APP_URL))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 Добро пожаловать, *{user.first_name}*!\n\n"
        f"Управляйте городом и зарабатывайте BossCoin (BSS) в нашем Mini App 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

def get_telegram_application() -> Application | None:
    """
    Создает и настраивает экземпляр Telegram Application.
    """
    if not TOKEN:
        logger.error("ОШИБКА: Токен бота (BOT_TOKEN) не установлен.")
        return None

    logger.info("Инициализация Telegram Application (Webhook Mode)...")
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start_command))

    return app_tg

# Инициализация Telegram Bot Application
tg_app = get_telegram_application()

# --- ФУНКЦИИ АУТЕНТИФИКАЦИИ И FIREBASE ---

def get_db_ref(user_id: str):
    """Получает ссылку на документ пользователя в Firestore."""
    if not db:
        # Если Firebase не инициализирован (нет ключа), все API-запросы провалятся
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
    
    if init_data == "debug_token_123":
        logger.warning("Используется заглушка токена 'debug_token_123'.")
        return {"uid": "debug_user_id"} 

    # Простая заглушка UID на основе init_data (в реальном приложении нужна полная верификация)
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
    """Загружает состояние пользователя из Firestore или создает новое."""
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
    """Сохраняет состояние пользователя в Firestore."""
    user_ref = get_db_ref(user_id)
    user_ref.set(state.model_dump())
    logger.info(f"Сохранено состояние для UID: {user_id}")

# --- ФУНКЦИЯ ДЛЯ УСТАНОВКИ WEBHOOK (Вынесена из startup_event) ---
async def set_telegram_webhook():
    """
    Выполняет установку вебхука асинхронно, не блокируя запуск Gunicorn.
    """
    if tg_app:
        base_url = os.getenv("BASE_URL")
        if base_url:
            webhook_url = f"{base_url}/bot_webhook"
            
            # Добавим небольшую случайную задержку
            await asyncio.sleep(random.uniform(0.1, 1.0))

            try:
                # !!! Принудительно устанавливаем вебхук.
                await tg_app.bot.set_webhook(url=webhook_url)
                logger.info(f"Установлен Telegram Webhook на: {webhook_url}")
            except telegram_error.RetryAfter as e:
                logger.warning(f"Ошибка Rate Limit при установке вебхука: {e}. Продолжаем работу.")
            except Exception as e:
                 logger.error(f"Непредвиденная ошибка при установке вебхука: {e}")
        else:
            logger.warning("BASE_URL не установлен. Webhook не установлен.")

# --- НАСТРОЙКА WEBHOOK ---
if tg_app:
    @app.on_event("startup")
    async def startup_event():
        try:
            await tg_app.initialize()
            logger.info("Telegram Application инициализирован для асинхронной работы.")
        except Exception as e:
            logger.error(f"Ошибка при инициализации Telegram Application: {e}")
        
        asyncio.create_task(set_telegram_webhook())
        logger.info("Задача установки Webhook запущена в фоне.")


    # Точка входа для Telegram Webhook
    @app.post("/bot_webhook")
    async def telegram_webhook(request: Request):
        try:
            body = await request.json()
            # Лог для отладки
            logger.info(f"Получен входящий JSON от Telegram: {json.dumps(body)}")
            
            update_obj = Update.de_json(data=body, bot=tg_app.bot) 
            
            # --- ИСПРАВЛЕНИЕ: Используем process_update вместо post_update ---
            # process_update запускает обработчики (CommandHandler)
            await tg_app.process_update(update_obj) 
            
            return {"status": "ok"}
        except Exception as e:
            # Логируем ошибку, но возвращаем 200 OK, чтобы Telegram не переотправлял обновление
            logger.error(f"Ошибка обработки вебхука Telegram: {e}")
            return {"status": "error", "message": str(e)}, 200 # Возвращаем 200 для Telegram

# --- API ЭНДПОИНТЫ ДЛЯ ИГРЫ ---
@app.post("/api/load_state")
async def load_state(request: Request):
    """Загружает состояние игры и применяет пассивный доход."""
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
    """Собирает пассивный доход."""
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
    """Покупает один сектор."""
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
        
        # Расчет и сбор дохода перед покупкой
        collected_income, current_time = calculate_income(state)
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()

        # Выполнение покупки
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

@app.get("/health_check")
def read_root():
    """Простой ответ для проверки работоспособности (health check)."""
    return {"status": "ok", "message": "TashBoss Clicker API is running."}

# Обслуживание статических файлов (index.html, app.js, style.css)
app.mount("/", StaticFiles(directory=".", html=True), name="static")
