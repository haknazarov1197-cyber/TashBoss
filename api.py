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

# Импорты для Firebase/Firestore - УДАЛЕНЫ ДЛЯ ТЕСТА
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, error as telegram_error 
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# КРИТИЧЕСКИ ВАЖНО: Инициализация 'app' на верхнем уровне для Gunicorn
app = FastAPI(title="TashBoss Clicker API (MOCK)", description="Backend for Telegram Mini App (MOCK DB)")
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
TOKEN = os.getenv("BOT_TOKEN") 
BASE_URL = os.getenv("BASE_URL") or "https://tashboss.onrender.com"
WEB_APP_URL = f"{BASE_URL}" 

# --- СХЕМЫ ДАННЫХ ---
class UserState(BaseModel):
    balance: float = Field(default=0.0)
    sectors: Dict[str, int] = Field(default_factory=lambda: {"sector1": 0, "sector2": 0, "sector3": 0})
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

# -------------------------------------------------------------
# --- MOCK DATABASE (Заглушка) ---
# Словарь для хранения состояний в памяти (не сохраняется при перезапуске)
MOCK_DB: Dict[str, UserState] = {}

async def load_or_create_state_mock(user_id: str) -> UserState:
    """Загружает состояние пользователя из MOCK_DB или создает новое."""
    if user_id in MOCK_DB:
        state = MOCK_DB[user_id]
        logger.info(f"Загружено MOCK-состояние для UID: {user_id}")
    else:
        # Стартовый капитал для теста
        state = UserState(balance=5000.0) 
        MOCK_DB[user_id] = state
        logger.info(f"Создано новое MOCK-состояние со стартовым капиталом для UID: {user_id}")
        
    return state

async def save_state_mock(user_id: str, state: UserState):
    """Сохраняет состояние пользователя в MOCK_DB."""
    MOCK_DB[user_id] = state
    logger.info(f"Сохранено MOCK-состояние для UID: {user_id}")

# -------------------------------------------------------------


# --- ЛОГИКА ТЕЛЕГРАМ БОТА ---

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

# --- ФУНКЦИИ АУТЕНТИФИКАЦИИ ---

async def get_auth_data(request: Request) -> dict:
    """Верифицирует токен Telegram Mini App из заголовка Authorization."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует или неверный заголовок авторизации."
        )

    init_data = auth_header.split(" ")[1]
    
    # Заглушка UID на основе init_data
    import hashlib
    # Используем заглушку, чтобы не зависеть от полной валидации токена Telegram
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

# --- ФУНКЦИЯ ДЛЯ УСТАНОВКИ WEBHOOK ---
async def set_telegram_webhook():
    """
    Выполняет установку вебхука асинхронно, не блокируя запуск Gunicorn.
    """
    if tg_app:
        base_url = os.getenv("BASE_URL")
        if base_url:
            webhook_url = f"{base_url}/bot_webhook"
            
            await asyncio.sleep(random.uniform(0.1, 1.0))

            try:
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


    @app.post("/bot_webhook")
    async def telegram_webhook(request: Request):
        try:
            body = await request.json()
            logger.info(f"Получен входящий JSON от Telegram: {json.dumps(body)}")
            
            update_obj = Update.de_json(data=body, bot=tg_app.bot) 
            
            await tg_app.process_update(update_obj) 
            
            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука Telegram: {e}")
            return {"status": "error", "message": str(e)}, 200 # Возвращаем 200 для Telegram

# --- API ЭНДПОИНТЫ ДЛЯ ИГРЫ ---
@app.post("/api/load_state")
async def load_state(request: Request):
    """Загружает состояние игры и применяет пассивный доход, используя MOCK DB."""
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data.get("uid")

        state = await load_or_create_state_mock(user_id)
        collected_income, current_time = calculate_income(state)
        
        # Обновляем состояние в памяти
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()
        
        await save_state_mock(user_id, state) # Сохраняем в MOCK_DB

        return {"status": "ok", "state": state.model_dump(), "collected_income": collected_income}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в load_state: {e}")
        # Возвращаем 500, но теперь она должна быть вызвана чем-то другим, а не Firebase
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при загрузке состояния (MOCK DB).")


@app.post("/api/collect_income")
async def collect_income(request: Request):
    """Собирает пассивный доход, используя MOCK DB."""
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data.get("uid")

        state = await load_or_create_state_mock(user_id)
        collected_income, current_time = calculate_income(state)
        
        # Обновляем состояние в памяти
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()

        await save_state_mock(user_id, state) # Сохраняем в MOCK_DB
        
        return {"status": "ok", "state": state.model_dump(), "collected_income": collected_income}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в collect_income: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при сборе дохода (MOCK DB).")

@app.post("/api/buy_sector")
async def buy_sector(req: BuySectorRequest, request: Request):
    """Покупает один сектор, используя MOCK DB."""
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data.get("uid")
        sector_name = req.sector

        if sector_name not in SECTOR_COSTS:
            raise HTTPException(status_code=400, detail="Неверное название сектора.")

        state = await load_or_create_state_mock(user_id)
        current_count = state.sectors.get(sector_name, 0)
        
        # Стоимость = Базовая стоимость * (Количество + 1)
        cost = SECTOR_COSTS[sector_name] * (current_count + 1)
        
        if state.balance < cost:
            raise HTTPException(status_code=400, detail="Недостаточно средств для покупки.")
        
        # Расчет и сбор дохода перед покупкой
        collected_income, current_time = calculate_income(state)
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()

        # Выполнение покупки
        state.balance -= cost
        state.sectors[sector_name] = state.sectors.get(sector_name, 0) + 1

        await save_state_mock(user_id, state) # Сохраняем в MOCK_DB

        return {"status": "ok", "state": state.model_dump()}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в buy_sector: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при покупке сектора (MOCK DB).")

# --- ОБСЛУЖИВАНИЕ СТАТИЧЕСКИХ ФАЙЛОВ И WEBAPP ---

@app.get("/health_check")
def read_root():
    """Простой ответ для проверки работоспособности (health check)."""
    return {"status": "ok", "message": "TashBoss Clicker API is running (MOCK DB)."}

# Обслуживание статических файлов (index.html, app.js, style.css)
app.mount("/", StaticFiles(directory=".", html=True), name="static")
