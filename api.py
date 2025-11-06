import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

# FastAPI & Starlette Imports
from fastapi import FastAPI, Depends, HTTPException, status, Header, Request, Body
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

# Firebase Admin Imports
import firebase_admin
from firebase_admin import credentials, auth, firestore

# Telegram Bot Imports
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, ApplicationBuilder

# --------------------------
# 1. КОНФИГУРАЦИЯ & КОНСТАНТЫ
# --------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# Константы окружения
# __app_id - зарезервированная переменная Canvas, используем ее для Firestore пути
__app_id = "tashboss" 
FIREBASE_APP = None
DB: firestore.client = None
TELEGRAM_APP: Application = None

# Game Config (должен совпадать с app.js)
SECTORS_CONFIG = {
    "sector1": {"passive_income": 0.5, "base_cost": 100},
    "sector2": {"passive_income": 2.0, "base_cost": 500},
    "sector3": {"passive_income": 10.0, "base_cost": 2500},
}
# Путь к коллекции Firestore
TASHBOSS_CLICKER_COLLECTION = "tashboss_clicker"

# Переменные окружения для бота
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = os.environ.get("BASE_URL", "https://tashboss.onrender.com")

# --------------------------
# 2. УТИЛИТЫ FIREBASE
# --------------------------

def init_firebase():
    """
    Инициализирует Firebase Admin SDK.
    """
    global FIREBASE_APP, DB
    FIREBASE_KEY_VAR = 'FIREBASE_SERVICE_ACCOUNT_KEY'
    key_str = os.environ.get(FIREBASE_KEY_VAR)

    if not key_str:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{FIREBASE_KEY_VAR}' не найдена.")
        sys.exit(1)

    try:
        service_account_info = json.loads(key_str.strip())
        cred = credentials.Certificate(service_account_info)
        
        # Если приложение уже инициализировано (например, при перезапуске gunicorn), 
        # то пропускаем инициализацию, чтобы избежать ошибки.
        if not firebase_admin._apps:
            FIREBASE_APP = firebase_admin.initialize_app(cred)
            logger.info("✅ Firebase Admin SDK успешно инициализирован.")
        else:
            FIREBASE_APP = firebase_admin.get_app()
            logger.info("✅ Firebase Admin SDK уже был инициализирован.")
            
        DB = firestore.client()
        logger.info("✅ Firestore Client готов к работе.")
        
    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ключ не является корректным JSON после очистки: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать Firebase Admin SDK: {e}")
        sys.exit(1)

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    if not DB:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database not initialized.")
        
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return DB.collection("artifacts").document(__app_id)\
             .collection("users").document(user_id)\
             .collection(TASHBOSS_CLICKER_COLLECTION).document(user_id)

# --------------------------
# 3. АУТЕНТИФИКАЦИЯ (FastAPI Dependency)
# --------------------------

def get_auth_data(authorization: str = Header(None)):
    """
    Проверяет Firebase ID Token и возвращает UID пользователя.
    Используется как зависимость для всех игровых API.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token."
        )
    
    token = authorization.split(" ")[1]
    
    try:
        # Проверяем Firebase ID token
        decoded_token = auth.verify_id_token(token)
        return decoded_token["uid"]
    except Exception as e:
        logger.error(f"Firebase ID Token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token."
        )

# --------------------------
# 4. ЛОГИКА TELEGRAM БОТА
# --------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /start."""
    # WebApp URL: BASE_URL (https://tashboss.onrender.com)
    webapp_url = BASE_URL
    
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
        "👋 Добро пожаловать! Нажмите кнопку, чтобы запустить TashBoss Clicker (Telegram Mini App).",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /help."""
    await update.message.reply_text(
        "Я бот-кликер TashBoss. Развивайте свою компанию, покупая сектора и собирая пассивный доход.\n\n"
        "Начните с команды /start."
    )

async def echo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает любое другое текстовое сообщение."""
    if update.message:
        await update.message.reply_text(f"Я не понимаю эту команду. Попробуйте /start.")

def setup_telegram_application() -> ApplicationBuilder:
    """Создает и настраивает ApplicationBuilder для бота."""
    
    # Используем ApplicationBuilder
    app_builder = ApplicationBuilder().token(BOT_TOKEN)

    # Добавляем обработчики команд
    app_builder.add_handler(CommandHandler("start", start_command))
    app_builder.add_handler(CommandHandler("help", help_command))
    
    # Обработчик текстовых сообщений
    app_builder.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_message))

    return app_builder

# --------------------------
# 5. НАСТРОЙКА FASTAPI ПРИЛОЖЕНИЯ
# --------------------------

app = FastAPI(
    title="Tashboss API Service",
    version="1.0.0",
    description="Backend service for Tashboss WebApp and Telegram Webhook."
)

# CRITICAL: Добавляем CORS Middleware для работы в WebApp (iframe)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем все источники
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------
# 6. LIFESPAN EVENTS
# --------------------------

@app.on_event("startup")
async def startup_event_full():
    """Инициализирует Firebase и Telegram Bot."""
    logger.info("Запуск функции FastAPI startup_event...")
    
    # 1. Инициализация Firebase
    init_firebase()
    
    # 2. Настройка Telegram Bot
    global TELEGRAM_APP
    if BOT_TOKEN:
        TELEGRAM_APP = setup_telegram_application().build()
        logger.info("✅ Telegram Application собран.")
        
        # 3. Установка Webhook
        webhook_url = f"{BASE_URL}/webhook"
        try:
            await TELEGRAM_APP.bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Webhook установлен на {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Не удалось установить Webhook. Убедитесь, что BASE_URL указан: {e}")
    else:
        logger.warning("Переменная BOT_TOKEN не установлена. Webhook и команды бота работать не будут.")

@app.on_event("shutdown")
def shutdown_event():
    """Вызывается при завершении работы приложения."""
    logger.info("Завершение работы приложения.")

# --------------------------
# 7. ЭНДПОЙНТЫ АУТЕНТИФИКАЦИИ И WEBHOOK
# --------------------------

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Обрабатывает входящие Telegram Updates."""
    if not TELEGRAM_APP:
        # 503 Service Unavailable, если бот не инициализирован
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialized.")

    # Получаем данные JSON из запроса
    data = await request.json()
    
    # Создаем и обрабатываем Update
    update = Update.de_json(data, TELEGRAM_APP.bot)
    await TELEGRAM_APP.process_update(update)
    
    return JSONResponse(content={"status": "ok"})

@app.post("/auth-token")
async def get_custom_token(data: dict = Body(..., embed=False)):
    """Обменивает Telegram User ID на Firebase Custom Auth Token."""
    telegram_user_id = data.get("telegram_user_id")
    
    if not telegram_user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Missing telegram_user_id")

    try:
        # Создаем Custom Token для аутентификации в клиенте Firebase
        custom_token = auth.create_custom_token(telegram_user_id)
        
        # Возвращаем декодированную строку
        return {"token": custom_token.decode('utf-8')}
    except Exception as e:
        logger.error(f"Error creating custom token for {telegram_user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create authentication token.")

# --------------------------
# 8. ЛОГИКА ИГРЫ (API)
# --------------------------

api_router = APIRouter(prefix="/api")

@firestore.transactional
def load_or_init_state_transaction(transaction, user_doc_ref, user_id):
    """Загружает или инициализирует состояние игры в транзакции."""
    
    doc = user_doc_ref.get(transaction=transaction)
    now = datetime.now(timezone.utc)

    if doc.exists:
        state = doc.to_dict()
        last_collection_time = state.get("last_collection_time", now)
        
        # Если `last_collection_time` — это метка времени Firestore, 
        # преобразуем ее в объект datetime
        if not isinstance(last_collection_time, datetime):
             last_collection_time = last_collection_time.astimezone(timezone.utc)
        
        # 1. Расчет пассивного дохода
        total_income_per_sec = sum(
            SECTORS_CONFIG[s]["passive_income"] * state["sectors"].get(s, 0)
            for s in state["sectors"]
        )
        
        time_diff = now - last_collection_time
        seconds_passed = time_diff.total_seconds()
        
        # Обновляем доступный доход
        available_income = state.get("available_income", 0) + (seconds_passed * total_income_per_sec)
        
        # Обновляем состояние в памяти, но не записываем в Firestore (только при collect_income)
        state["available_income"] = available_income
        state["last_collection_time"] = now # Обновляем время только для предотвращения "двойного" дохода
        state["user_id"] = user_id
        
        # Обновляем документ в Firestore, чтобы зафиксировать последнее время 
        # и предотвратить накопление дохода, если пользователь не "собирает" его.
        # Это предотвращает эксплойты при многократной загрузке.
        transaction.update(user_doc_ref, {"last_collection_time": now, "available_income": available_income})
        
        return state
        
    else:
        # Инициализация нового состояния
        initial_state = {
            "user_id": user_id,
            "balance": 100.0,
            "sectors": {"sector1": 0, "sector2": 0, "sector3": 0},
            "last_collection_time": now,
            "available_income": 0.0,
            "total_earnings": 0.0 # Для будущей статистики
        }
        transaction.set(user_doc_ref, initial_state)
        return initial_state


@api_router.post("/load_state")
async def load_state(user_id: str = Depends(get_auth_data)):
    """Загружает или инициализирует состояние игры пользователя."""
    transaction = DB.transaction()
    user_doc_ref = get_user_doc_ref(user_id)
    
    try:
        # Используем транзакционную функцию
        state = load_or_init_state_transaction(transaction, user_doc_ref, user_id)
        # Удаляем last_collection_time, чтобы не смущать фронтенд
        state.pop("last_collection_time", None) 
        return state
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке/инициализации состояния для {user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to load game state.")

# --- Функция сбора дохода (Транзакция) ---

@firestore.transactional
def collect_income_transaction(transaction, user_doc_ref):
    """Рассчитывает и собирает доступный доход."""
    doc = user_doc_ref.get(transaction=transaction)
    
    if not doc.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Game state not found.")
        
    state = doc.to_dict()
    now = datetime.now(timezone.utc)
    
    # 1. Расчет накопленного дохода (повторяем расчет из load_state для точности)
    last_collection_time = state.get("last_collection_time", now)
    if not isinstance(last_collection_time, datetime):
         last_collection_time = last_collection_time.astimezone(timezone.utc)
         
    total_income_per_sec = sum(
        SECTORS_CONFIG[s]["passive_income"] * state["sectors"].get(s, 0)
        for s in state["sectors"]
    )
    time_diff = now - last_collection_time
    seconds_passed = time_diff.total_seconds()
    
    # Общий доступный доход: старый доступный + новый накопленный
    available_income = state.get("available_income", 0) + (seconds_passed * total_income_per_sec)
    
    # 2. Сбор: перенос доступного дохода на баланс
    collected_amount = available_income
    new_balance = state["balance"] + collected_amount
    
    # 3. Обновление документа в Firestore
    update_data = {
        "balance": new_balance,
        "available_income": 0.0, # Обнуляем доступный доход
        "last_collection_time": now,
        "total_earnings": state.get("total_earnings", 0.0) + collected_amount
    }
    transaction.update(user_doc_ref, update_data)
    
    # Обновляем состояние для ответа фронтенду
    state["balance"] = new_balance
    state["available_income"] = 0.0
    state["collected_amount"] = collected_amount
    state.pop("last_collection_time", None)
    
    return state
    
    
@api_router.post("/collect_income")
async def collect_income(user_id: str = Depends(get_auth_data)):
    """Собирает пассивный доход и добавляет его к балансу."""
    transaction = DB.transaction()
    user_doc_ref = get_user_doc_ref(user_id)
    
    try:
        return collect_income_transaction(transaction, user_doc_ref)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка при сборе дохода для {user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to collect income.")

# --- Функция покупки сектора (Транзакция) ---

@firestore.transactional
def buy_sector_transaction(transaction, user_doc_ref, sector_id):
    """Покупает следующий уровень сектора."""
    doc = user_doc_ref.get(transaction=transaction)

    if not doc.exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Game state not found.")
        
    state = doc.to_dict()
    now = datetime.now(timezone.utc)
    
    # 1. Сначала собираем любой доступный доход
    last_collection_time = state.get("last_collection_time", now)
    if not isinstance(last_collection_time, datetime):
         last_collection_time = last_collection_time.astimezone(timezone.utc)
         
    total_income_per_sec = sum(
        SECTORS_CONFIG[s]["passive_income"] * state["sectors"].get(s, 0)
        for s in state["sectors"]
    )
    time_diff = now - last_collection_time
    seconds_passed = time_diff.total_seconds()
    available_income = state.get("available_income", 0) + (seconds_passed * total_income_per_sec)
    
    collected_before_purchase = available_income
    state["balance"] += collected_before_purchase
    
    # 2. Логика покупки
    current_level = state["sectors"].get(sector_id, 0)
    config = SECTORS_CONFIG.get(sector_id)
    
    if not config:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid sector ID.")
        
    # Стоимость = BaseCost * (Текущий_Уровень + 1)
    cost = config["base_cost"] * (current_level + 1)
    
    if state["balance"] < cost:
        # Возвращаем состояние, чтобы фронтенд знал, сколько было собрано, но покупка не удалась.
        state["collected_amount"] = collected_before_purchase
        state["purchase_successful"] = False
        state.pop("last_collection_time", None)
        return state
        
    # Выполнение покупки
    new_balance = state["balance"] - cost
    new_level = current_level + 1
    
    # 3. Обновление документа в Firestore
    update_data = {
        "balance": new_balance,
        "sectors": {**state["sectors"], sector_id: new_level},
        "available_income": 0.0, # Доход обнулен, так как он был собран/учтен
        "last_collection_time": now,
        "total_earnings": state.get("total_earnings", 0.0) + collected_before_purchase # Учитываем собранное
    }
    
    # Используем update для обновления вложенного поля 'sectors'
    transaction.update(user_doc_ref, update_data)
    
    # Обновляем состояние для ответа фронтенду
    state["balance"] = new_balance
    state["sectors"] = update_data["sectors"]
    state["available_income"] = 0.0
    state["collected_amount"] = collected_before_purchase
    state["purchase_successful"] = True
    state.pop("last_collection_time", None)
    
    return state


@api_router.post("/buy_sector")
async def buy_sector(user_id: str = Depends(get_auth_data), data: dict = Body(...)):
    """Покупает следующий уровень сектора."""
    sector_id = data.get("sector_id")
    
    if not sector_id or sector_id not in SECTORS_CONFIG:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid sector ID provided.")
        
    transaction = DB.transaction()
    user_doc_ref = get_user_doc_ref(user_id)
    
    try:
        return buy_sector_transaction(transaction, user_doc_ref, sector_id)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка при покупке сектора {sector_id} для {user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to purchase sector.")


# Добавляем маршруты API
app.include_router(api_router)

# --------------------------
# 9. ОБСЛУЖИВАНИЕ СТАТИЧЕСКИХ ФАЙЛОВ
# --------------------------

# Обслуживаем статические файлы (index.html, app.js) из корневой директории
# CRITICAL: StaticFiles должен быть добавлен в конце, чтобы не перехватывать API-маршруты.
app.mount("/", StaticFiles(directory=".", html=True), name="static")

# Дополнительный маршрут для WebApp, который возвращает index.html
@app.get("/webapp")
async def serve_webapp():
    # FastAPI's StaticFiles с html=True автоматически обслуживает index.html для /
    # Это просто для наглядности, но маршрут / уже работает.
    # Если Render или Nginx настроен на поиск /webapp, эта заглушка может помочь:
    return app.get("/", response_class=JSONResponse) # Просто возвращаем index.html, обслуживаемый StaticFiles
