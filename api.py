import os
import sys
import json
import logging
import httpx # Используем httpx для асинхронных HTTP-запросов к Telegram API
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timedelta, timezone

# FastAPI и инструменты
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, auth, firestore
from google.cloud.firestore import Client, Transaction

# --- Настройка логгера ---
logger = logging.getLogger("api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
# -------------------------

# --- Глобальные переменные ---
FIREBASE_APP = None
DB_CLIENT: Client | None = None
# ИДЕНТИФИКАТОР ПРОЕКТА, СООТВЕТСТВУЮЩИЙ FIREBASE KEY
APP_ID = "tashboss-1bd35" 
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# --- Конфигурация Игры ---
SECTORS_CONFIG = {
    "sector1": {"passive_income": 0.5, "base_cost": 100.0},
    "sector2": {"passive_income": 2.0, "base_cost": 500.0},
    "sector3": {"passive_income": 10.0, "base_cost": 2500.0},
}
INITIAL_BALANCE = 100.0
# ---------------------------

# --- Pydantic Схемы ---
class BuySectorRequest(BaseModel):
    sector_id: str

class GameState(BaseModel):
    user_id: str
    balance: float
    sectors: dict[str, int]
    last_collection_time: datetime
    available_income: float = 0.0
    purchase_successful: bool = False
    collected_amount: float = 0.0

class TelegramAuthRequest(BaseModel):
    init_data: str # Строка initData, переданная WebApp

class FirebaseTokenResponse(BaseModel):
    firebase_token: str
    uid: str

# Схемы для Webhook
class TelegramMessage(BaseModel):
    text: str | None = None
    chat: dict
    from_user: dict | None = None

class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None

# --- Инициализация Firebase ---

def init_firebase():
    """Инициализирует Firebase Admin SDK и клиента Firestore."""
    global FIREBASE_APP, DB_CLIENT
    
    key_string = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    if not key_string:
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY отсутствует.")
        sys.exit(1)
        
    try:
        # Убедитесь, что строка ключа корректно обрабатывается
        cleaned_key_string = key_string.strip().strip("'\"").replace('\n', '').replace('\r', '')
        service_account_info = json.loads(cleaned_key_string)

        if not firebase_admin._apps:
            cred = credentials.Certificate(service_account_info)
            FIREBASE_APP = firebase_admin.initialize_app(cred)
            DB_CLIENT = firestore.client(FIREBASE_APP)
            logger.info("✅ Ключ Firebase успешно загружен и Firebase инициализирован.")
        
    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Сбой декодирования JSON для ключа Firebase: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Неожиданная ошибка инициализации: {type(e).__name__}: {e}")
        sys.exit(1)

# --- Настройка FastAPI ---

app = FastAPI(title="TashBoss Clicker API")

# 1. CORS Middleware (КРИТИЧНО для Telegram WebApp)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Обслуживание статических файлов (index.html, app.js)
app.mount("/app.js", StaticFiles(directory=".", html=False), name="app_js")
app.mount("/favicon.ico", StaticFiles(directory=".", html=False), name="favicon")

# --- Утилиты Telegram ---

def get_base_url(request: Request) -> str:
    """Определяет базовый URL для WebApp (нужен для кнопки)."""
    # Render предоставляет правильный публичный URL
    host = request.headers.get("X-Forwarded-Host") or request.url.netloc
    scheme = request.headers.get("X-Forwarded-Proto") or request.url.scheme
    return f"{scheme}://{host}"

async def send_telegram_message(chat_id: int, text: str, web_app_url: str):
    """Отправляет сообщение с кнопкой WebApp."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не установлен. Не могу отправить сообщение.")
        return

    # Клавиатура с кнопкой WebApp
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "🚀 Запустить TashBoss Clicker",
                    "web_app": {"url": web_app_url}
                }
            ]
        ]
    }
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": reply_markup,
        "parse_mode": "Markdown"
    }

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.post(api_url, json=payload)
            response.raise_for_status()
            logger.info(f"✅ Сообщение Telegram отправлено в чат {chat_id}.")
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ Ошибка HTTP при отправке сообщения Telegram: {e.response.text}")
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при отправке сообщения Telegram: {e}")


def check_telegram_init_data(init_data: str) -> Dict[str, Any] | None:
    """
    Проверяет Telegram initData по алгоритму, описанному в документации.
    Возвращает словарь данных, если проверка успешна, иначе None.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не установлен. Проверка initData невозможна.")
        return None
        
    try:
        # 1. Секретный ключ для HMAC SHA-256
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=TELEGRAM_BOT_TOKEN.encode(),
            digestmod=hashlib.sha256
        ).digest()

        # 2. Разбор init_data
        parsed_data = urllib.parse.parse_qs(init_data)
        
        # 3. Извлечение hash и создание списка данных для проверки
        hash_to_check = parsed_data.pop('hash', [None])[0]
        
        if not hash_to_check:
            logger.warning("Telegram initData не содержит hash.")
            return None

        # 4. Создание строки data_check_string
        data_check_list = []
        for key in sorted(parsed_data.keys()):
            # Исключаем 'hash' из data_check_string
            if key != 'hash':
                # urllib.parse.qs возвращает список, берем первый элемент
                value = parsed_data[key][0]
                data_check_list.append(f"{key}={value}")
        
        data_check_string = "\n".join(data_check_list)

        # 5. Вычисление HMAC
        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()

        # 6. Сравнение
        if calculated_hash.lower() == hash_to_check.lower():
            logger.info("✅ Telegram initData успешно проверен.")
            
            # Извлечение данных пользователя
            user_data_str = parsed_data.get('user', [None])[0]
            if user_data_str:
                user_data = json.loads(user_data_str)
                # Добавляем данные пользователя для удобства
                parsed_data['user_data'] = [user_data]
            
            # Возвращаем все разобранные данные
            return parsed_data
        else:
            logger.warning(f"❌ Неверный Telegram hash. Calculated: {calculated_hash}, Received: {hash_to_check}")
            return None

    except Exception as e:
        logger.error(f"❌ Ошибка проверки Telegram initData: {e}", exc_info=True)
        return None


# --- Аутентификация: Зависимость FastAPI ---

async def get_auth_data(request: Request) -> str:
    """Извлекает и проверяет токен Firebase ID, возвращает UID пользователя."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не предоставлен токен Bearer",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = auth_header.split(" ")[1]
    
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token.get('uid')
        return uid
    except Exception as e:
        logger.error(f"Ошибка проверки токена: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный токен Firebase ID",
        )

# --- Утилиты Firestore ---

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    if not DB_CLIENT:
        raise RuntimeError("DB_CLIENT не инициализирован.")
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    doc_path = f"artifacts/{APP_ID}/users/{user_id}/tashboss_clicker/{user_id}"
    return DB_CLIENT.document(doc_path)


def calculate_passive_income(game_data: dict) -> tuple[float, datetime]:
    """
    Рассчитывает пассивный доход, накопленный с last_collection_time.
    Возвращает (накопленный_доход, новое_время_сбора).
    """
    last_collection_time = game_data.get('last_collection_time')
    if not last_collection_time or not isinstance(last_collection_time, datetime):
        return 0.0, datetime.now(timezone.utc)

    if last_collection_time.tzinfo is None:
        last_collection_time = last_collection_time.replace(tzinfo=timezone.utc)

    current_time = datetime.now(timezone.utc)
    
    max_time_delta = timedelta(days=7)
    time_delta = current_time - last_collection_time

    if time_delta > max_time_delta:
        time_delta = max_time_delta
        
    total_seconds = time_delta.total_seconds()
    
    total_income_per_second = 0.0
    sectors = game_data.get('sectors', {})
    for sector_id, level in sectors.items():
        config = SECTORS_CONFIG.get(sector_id)
        if config and level > 0:
            total_income_per_second += config["passive_income"] * level
            
    accumulated_income = total_income_per_second * total_seconds
    
    new_collection_time = current_time 

    return round(accumulated_income, 2), new_collection_time

# --- Логика Игры (Транзакции) ---

@firestore.transactional
def get_or_create_state_transaction(transaction: Transaction, doc_ref, user_id: str) -> dict:
    """Получает состояние или создает новое в транзакции."""
    doc = doc_ref.get(transaction=transaction)
    
    if doc.exists:
        data = doc.to_dict()
    else:
        data = {
            "user_id": user_id,
            "balance": INITIAL_BALANCE,
            "sectors": {k: 0 for k in SECTORS_CONFIG},
            "last_collection_time": datetime.now(timezone.utc),
        }
        transaction.set(doc_ref, data)
        
    return data


@firestore.transactional
def collect_income_transaction(transaction: Transaction, doc_ref, game_data: dict) -> tuple[dict, float]:
    """Собирает пассивный доход и обновляет баланс в транзакции."""
    accumulated_income, new_time = calculate_passive_income(game_data)
    
    if accumulated_income > 0.0:
        new_balance = game_data['balance'] + accumulated_income
        
        updates = {
            "balance": round(new_balance, 2),
            "last_collection_time": new_time,
        }
        transaction.update(doc_ref, updates)
        
        game_data.update(updates)
        return game_data, accumulated_income
        
    updates = {"last_collection_time": new_time}
    transaction.update(doc_ref, updates)
    game_data.update(updates)
    return game_data, 0.0


@firestore.transactional
def buy_sector_transaction(transaction: Transaction, doc_ref, game_data: dict, sector_id: str) -> tuple[dict, bool, float]:
    """Покупает следующий уровень сектора в транзакции."""
    game_data, collected_amount = collect_income_transaction(transaction, doc_ref, game_data)

    current_level = game_data['sectors'].get(sector_id, 0)
    config = SECTORS_CONFIG.get(sector_id)

    if not config:
        return game_data, False, collected_amount
        
    cost = config["base_cost"] * (current_level + 1)
    
    if game_data['balance'] >= cost:
        new_balance = game_data['balance'] - cost
        new_level = current_level + 1
        
        game_data['sectors'][sector_id] = new_level
        
        updates = {
            "balance": round(new_balance, 2),
            f"sectors.{sector_id}": new_level,
        }
        
        transaction.update(doc_ref, updates)
        
        game_data.update(updates)
        return game_data, True, collected_amount
    
    return game_data, False, collected_amount

# --- Эндпоинты API ---

@app.on_event("startup")
async def startup_event():
    """Обработчик события запуска приложения: инициализация Firebase."""
    logger.info("Запуск приложения...")
    init_firebase()
    
# Обслуживание index.html по корневому пути и /webapp
@app.get("/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_index():
    """Обслуживает index.html."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Файл index.html не найден.")


@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate, request: Request):
    """
    Принимает обновления от Telegram и обрабатывает команду /start.
    """
    if update.message and update.message.text:
        text = update.message.text.strip()
        chat_id = update.message.chat['id']
        
        # Обработка команды /start
        if text.startswith("/start"):
            logger.info(f"Получена команда /start от чата {chat_id}.")
            
            # Базовый URL вашего Render-сервиса
            base_url = get_base_url(request)
            web_app_url = f"{base_url}/webapp"

            welcome_message = (
                "Добро пожаловать в *TashBoss Clicker*!\n\n"
                "Здесь вы можете развивать свой бизнес и зарабатывать BossCoin.\n"
                "Нажмите кнопку ниже, чтобы начать играть!"
            )
            
            await send_telegram_message(chat_id, welcome_message, web_app_url)
            
            return JSONResponse({"status": "success", "message": "Command processed"})
        
        logger.info(f"Получено сообщение от чата {chat_id}: {text}")

    return JSONResponse({"status": "ignored", "message": "No action required"})


@app.post("/api/get_firebase_token", response_model=FirebaseTokenResponse)
async def get_token_for_webapp(request_data: TelegramAuthRequest):
    """
    Проверяет init_data от Telegram и генерирует кастомный токен Firebase.
    ЭТО НОВЫЙ КРИТИЧЕСКИЙ ЭНДПОИНТ.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не установлен. Отказ в аутентификации WebApp.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Токен бота не установлен на сервере."
        )

    # 1. Проверка initData
    parsed_data = check_telegram_init_data(request_data.init_data)
    
    if not parsed_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительные или просроченные данные Telegram InitData."
        )

    # 2. Извлечение user ID
    user_data = parsed_data.get('user_data', [{}])[0]
    
    # Telegram user ID является UID для Firebase
    tg_user_id = str(user_data.get('id')) 
    if not tg_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Данные Telegram не содержат ID пользователя."
        )
    
    # 3. Генерация кастомного токена Firebase
    try:
        # Аутентификация использует уникальный ID пользователя Telegram
        firebase_token = auth.create_custom_token(tg_user_id).decode('utf-8')
        logger.info(f"✅ Создан кастомный токен Firebase для TG ID: {tg_user_id}")
        
        return FirebaseTokenResponse(
            firebase_token=firebase_token,
            uid=tg_user_id
        )
    except Exception as e:
        logger.error(f"❌ Ошибка генерации токена Firebase для {tg_user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании токена Firebase."
        )


@app.post("/api/load_state", response_model=GameState)
async def load_state(user_id: str = Depends(get_auth_data)):
    """Загружает или создает состояние игры и рассчитывает доступный доход."""
    doc_ref = get_user_doc_ref(user_id)
    transaction = DB_CLIENT.transaction()
    
    try:
        game_data = get_or_create_state_transaction(transaction, doc_ref, user_id)
        
        available_income, _ = calculate_passive_income(game_data)
        game_data['available_income'] = available_income
        
        return GameState(**game_data)
    
    except Exception as e:
        logger.error(f"Ошибка load_state для пользователя {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка загрузки состояния игры.")


@app.post("/api/collect_income", response_model=GameState)
async def collect_income(user_id: str = Depends(get_auth_data)):
    """Собирает накопленный пассивный доход."""
    doc_ref = get_user_doc_ref(user_id)
    transaction = DB_CLIENT.transaction()
    
    try:
        current_data = doc_ref.get().to_dict()
        if not current_data:
            raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
            
        updated_data, collected_amount = collect_income_transaction(transaction, doc_ref, current_data)
        
        updated_data['available_income'] = 0.0
        updated_data['collected_amount'] = collected_amount
        
        return GameState(**updated_data)
        
    except Exception as e:
        logger.error(f"Ошибка collect_income для пользователя {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сбора дохода.")


@app.post("/api/buy_sector", response_model=GameState)
async def buy_sector(request: BuySectorRequest, user_id: str = Depends(get_auth_data)):
    """Покупает следующий уровень сектора."""
    doc_ref = get_user_doc_ref(user_id)
    transaction = DB_CLIENT.transaction()
    sector_id = request.sector_id
    
    if sector_id not in SECTORS_CONFIG:
        raise HTTPException(status_code=400, detail="Неверный идентификатор сектора.")
        
    try:
        current_data = doc_ref.get().to_dict()
        if not current_data:
            raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
            
        updated_data, success, collected_amount = buy_sector_transaction(transaction, doc_ref, current_data, sector_id)
        
        updated_data['available_income'] = 0.0
        updated_data['purchase_successful'] = success
        updated_data['collected_amount'] = collected_amount
        
        return GameState(**updated_data)
        
    except Exception as e:
        logger.error(f"Ошибка buy_sector для пользователя {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка покупки сектора.")
