import os
import sys
import json
import logging
from datetime import datetime, timedelta
import asyncio 
import random 
from typing import Dict
import math 

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

# КРИТИЧЕСКИЙ ФИКС: Явно указываем ID базы данных, так как она не "default"
# На основании скриншотов, ID базы данных - "tashboss"
DATABASE_ID = "tashboss"

# Переменные для отладки
PROJECT_ID = "N/A"
FIREBASE_INIT_STATUS = False
# --------------------

# Инициализация Firebase Admin SDK
db = None
def initialize_firebase():
    """Инициализация Firebase Admin SDK с использованием ключа из переменной окружения."""
    global db, PROJECT_ID, FIREBASE_INIT_STATUS
    
    if FIREBASE_KEY_JSON and not firebase_admin._apps:
        try:
            cleaned_json_string = FIREBASE_KEY_JSON.replace('\n', '').replace('\r', '').strip()
            cred_dict = json.loads(cleaned_json_string)
            PROJECT_ID = cred_dict.get('project_id', 'PROJECT_ID_MISSING_IN_KEY')
            
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            
            # --- КРИТИЧЕСКИЙ ФИКС ПРИМЕНЕН ЗДЕСЬ ---
            # Явно указываем ID базы данных при создании клиента
            db = firestore.client(database=DATABASE_ID)
            
            FIREBASE_INIT_STATUS = True
            logger.info(f"Firebase Admin SDK успешно инициализирован. Используется DB ID: {DATABASE_ID}")
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка JSONDecodeError при парсинге ключа Firebase: {e}. Проверьте форматирование ключа.")
            db = None
            FIREBASE_INIT_STATUS = False
        except Exception as e:
            logger.error(f"Непредвиденная ошибка инициализации Firebase Admin SDK: {e}")
            db = None
            FIREBASE_INIT_STATUS = False
    elif firebase_admin._apps:
        # Если приложение уже инициализировано, просто получаем клиента с нужным ID
        try:
             db = firestore.client(database=DATABASE_ID)
             PROJECT_ID = firebase_admin.get_app().project_id if firebase_admin.get_app().project_id else "UNKNOWN_FROM_APP"
             FIREBASE_INIT_STATUS = True
             logger.info(f"Firebase Admin SDK уже инициализирован. Используется DB ID: {DATABASE_ID}")
        except Exception as e:
            logger.error(f"Ошибка при получении клиента Firestore с ID {DATABASE_ID}: {e}")
            db = None
            FIREBASE_INIT_STATUS = False
    else:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_KEY не установлен. Firestore будет недоступен.")
        FIREBASE_INIT_STATUS = False

initialize_firebase()

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
# Множитель стоимости для экспоненциального роста (должен совпадать с app.js)
COST_MULTIPLIER = 1.15
MAX_IDLE_TIME = 10 * 24 * 3600 # 10 дней в секундах

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

# Инициализация Telegram Bot Application -- ПЕРЕНЕСЕНО СЮДА
tg_app = get_telegram_application() 

# --- ФУНКЦИИ АУТЕНТИФИКАЦИИ И FIREBASE ---

def get_db_ref(user_id: str):
    """Получает ссылку на документ пользователя в Firestore."""
    if not db:
        # Эта ошибка должна срабатывать только если Firebase не инициализирован
        raise HTTPException(status_code=500, detail="Firestore не инициализирован. Проверьте FIREBASE_SERVICE_ACCOUNT_KEY.")
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

# КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Оборачиваем синхронные вызовы Firestore в asyncio.to_thread
async def save_state(user_id: str, state: UserState):
    """Сохраняет состояние пользователя в Firestore, используя asyncio.to_thread."""
    user_ref = get_db_ref(user_id)
    # Оборачиваем синхронную сетевую операцию записи
    await asyncio.to_thread(user_ref.set, state.model_dump())
    logger.info(f"Сохранено состояние для UID: {user_id}")

async def load_or_create_state(user_id: str) -> UserState:
    """Загружает состояние пользователя из Firestore или создает новое с 5000 BSS, используя asyncio.to_thread."""
    user_ref = get_db_ref(user_id)
    
    # Оборачиваем синхронную сетевую операцию получения документа
    doc = await asyncio.to_thread(user_ref.get)

    if doc.exists:
        # to_dict - это локальная операция, не требует to_thread
        data = doc.to_dict()
        state = UserState(**data)
        logger.info(f"Загружено состояние для UID: {user_id}")
    else:
        # Добавление стартового капитала (5000 BSS)
        state = UserState(balance=5000.0) 
        await save_state(user_id, state) # save_state теперь тоже асинхронный
        logger.info(f"Создано новое состояние со стартовым капиталом для UID: {user_id}")
        
    return state
# КОНЕЦ КРИТИЧЕСКОГО ИСПРАВЛЕНИЯ

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

# --- API ЭНДПОИНТЫ ДЛЯ ИГРЫ (С ИЗМЕНЕНИЯМИ) ---
@app.post("/api/load_state")
async def load_state(request: Request):
    """Загружает состояние игры и применяет пассивный доход, используя Firestore."""
    try:
        # 1. ВРЕМЕННО ОТКЛЮЧАЕМ АУТЕНТИФИКАЦИЮ И ИСПОЛЬЗУЕМ СТАТИЧЕСКИЙ ID
        # auth_data = await get_auth_data(request)
        # user_id = auth_data.get("uid")
        user_id = "test_user_for_debug"
        # ---------------------------------------------------------------------

        # Теперь load_or_create_state полностью асинхронна и безопасна
        state = await load_or_create_state(user_id) 
        collected_income, current_time = calculate_income(state)
        
        state.balance += collected_income
        state.last_collection_time = current_time.isoformat()
        
        # Теперь save_state полностью асинхронна и безопасна
        await save_state(user_id, state) 

        return {"status": "ok", "state": state.model_dump(), "collected_income": collected_income}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка в load_state: {e}")
        # Возвращаем общую ошибку
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при загрузке состояния.")


@app.post("/api/collect_income")
async def collect_income(request: Request):
    """Собирает пассивный доход, используя Firestore."""
    try:
        # ВРЕМЕННО ОТКЛЮЧАЕМ АУТЕНТИФИКАЦИЮ
        # auth_data = await get_auth_data(request)
        # user_id = auth_data.get("uid")
        user_id = "test_user_for_debug"
        # --------------------------------

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
    """Покупает один сектор, используя Firestore."""
    try:
        # ВРЕМЕННО ОТКЛЮЧАЕМ АУТЕНТИФИКАЦИЮ
        # auth_data = await get_auth_data(request)
        # user_id = auth_data.get("uid")
        user_id = "test_user_for_debug"
        # --------------------------------

        sector_name = req.sector

        if sector_name not in SECTOR_COSTS:
            raise HTTPException(status_code=400, detail="Неверное название сектора.")

        # ПЕРЕРАСЧЕТ СТОИМОСТИ: стоимость должна расти с каждой покупкой
        state = await load_or_create_state(user_id)
        current_count = state.sectors.get(sector_name, 0)
        
        # Стоимость = Базовая стоимость * (Множитель в степени текущего уровня)
        base_cost = SECTOR_COSTS[sector_name]
        cost = base_cost * math.pow(COST_MULTIPLIER, current_count)

        # Округляем до целых чисел (или до 2 знаков после запятой, для точности)
        cost = round(cost, 2)
        
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

# --- ЭНДПОИНТ ДЛЯ ОТЛАДКИ FIREBASE ---
@app.get("/api/check_db")
async def check_database_status():
    """Проверяет статус инициализации Firebase Admin SDK."""
    if db is None:
        return {
            "status": "error", 
            "message": "❌ Firestore НЕ инициализирован.", 
            "details": "Проверьте переменную окружения FIREBASE_SERVICE_ACCOUNT_KEY: JSON, возможно, не валиден или содержит лишние символы."
        }
    else:
        # Попробуем сделать легкий запрос, чтобы убедиться, что он работает
        try:
            # Делаем асинхронный вызов к тестовому документу
            # Используем try/except для обработки ошибки 404
            await asyncio.to_thread(db.collection("health_check").document("status").get)
            
            return {
                "status": "ok", 
                "message": f"✅ Firestore (ID: {DATABASE_ID}) инициализирован и отвечает.", 
                "details": "Проблема, вероятно, в другой части кода (но аутентификация отключена, так что это почти гарантирует запуск)."
            }
        except Exception as e:
            return {
                "status": "warning", 
                "message": "⚠️ Firestore инициализирован, но запрос к нему не удался.", 
                "details": f"Возможно, проблема с сетью или правилами безопасности: {str(e)}"
            }

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ПОДРОБНОЙ ОТЛАДКИ ---
@app.get("/api/debug_info")
async def debug_info():
    """Возвращает подробную информацию о статусе инициализации Firebase и ID проекта."""
    
    # Проверка, была ли инициализация успешной
    if not FIREBASE_INIT_STATUS:
        return {
            "status": "critical_error",
            "message": "❌ Инициализация Firebase не удалась.",
            "project_id_from_key": PROJECT_ID,
            "details": "Ключ JSON не был корректно распарсен. Проверьте FIREBASE_SERVICE_ACCOUNT_KEY."
        }
        
    # Если инициализация успешна, пробуем сделать запрос к DB
    db_status = await check_database_status()
    
    return {
        "status": "ok_ready" if db_status["status"] == "ok" else db_status["status"],
        "message": f"✅ Бэкенд запущен и Firebase инициализирован (DB ID: {DATABASE_ID}).",
        "project_id_from_key": PROJECT_ID,
        "db_check_result": db_status["message"],
        "db_check_details": db_status["details"] if db_status["status"] != "ok" else "DB Check OK. Game should run with 'test_user_for_debug'."
    }
# КОНЕЦ НОВОГО ЭНДПОИНТА
    

# --- ОБСЛУЖИВАНИЕ СТАТИЧЕСКИХ ФАЙЛОВ И WEBAPP ---

@app.get("/health_check")
def read_root():
    """Простой ответ для проверки работоспособности (health check)."""
    return {"status": "ok", "message": "TashBoss Clicker API is running (Fixed Async Firestore and Disabled Auth)."}

# Обслуживание статических файлов (index.html, app.js, style.css)
app.mount("/", StaticFiles(directory=".", html=True), name="static")
