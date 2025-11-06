import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone

# FastAPI и инструменты
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
APP_ID = "tashboss-clicker-app" # Идентификатор проекта/приложения

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

# --- Инициализация Firebase ---

def init_firebase():
    """Инициализирует Firebase Admin SDK и клиента Firestore."""
    global FIREBASE_APP, DB_CLIENT
    
    # КЛЮЧ: Используем надежную очистку
    key_string = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    if not key_string:
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY отсутствует.")
        sys.exit(1)
        
    try:
        # Очистка: удаляем внешние кавычки и все символы новой строки/возврата каретки
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
# Разрешаем все, чтобы избежать проблем с доменами Telegram
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Обслуживание статических файлов (index.html, app.js)
# Сначала монтируем статические файлы (app.js)
app.mount("/app.js", StaticFiles(directory=".", html=False), name="app_js")
app.mount("/favicon.ico", StaticFiles(directory=".", html=False), name="favicon")

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
        # Проверка токена с помощью Firebase Admin SDK
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
        # Если время не установлено, сбора не было
        return 0.0, datetime.now(timezone.utc)

    # Убедимся, что время UTC-совместимо для расчетов
    if last_collection_time.tzinfo is None:
        last_collection_time = last_collection_time.replace(tzinfo=timezone.utc)

    current_time = datetime.now(timezone.utc)
    
    # Ограничиваем максимальное время для предотвращения эксплойтов (например, до 7 дней)
    max_time_delta = timedelta(days=7)
    time_delta = current_time - last_collection_time

    if time_delta > max_time_delta:
        time_delta = max_time_delta
        
    total_seconds = time_delta.total_seconds()
    
    # Расчет дохода в секунду
    total_income_per_second = 0.0
    sectors = game_data.get('sectors', {})
    for sector_id, level in sectors.items():
        config = SECTORS_CONFIG.get(sector_id)
        if config and level > 0:
            total_income_per_second += config["passive_income"] * level
            
    accumulated_income = total_income_per_second * total_seconds
    
    # Устанавливаем новое время сбора в текущее время (или время + max_time_delta, если ограничено)
    new_collection_time = current_time 

    # Округляем доход до двух знаков после запятой
    return round(accumulated_income, 2), new_collection_time

# --- Логика Игры (Транзакции) ---

@firestore.transactional
def get_or_create_state_transaction(transaction: Transaction, doc_ref, user_id: str) -> dict:
    """Получает состояние или создает новое в транзакции."""
    doc = doc_ref.get(transaction=transaction)
    
    if doc.exists:
        data = doc.to_dict()
    else:
        # Инициализация нового состояния
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
        
        # Обновление данных
        updates = {
            "balance": round(new_balance, 2),
            "last_collection_time": new_time,
        }
        transaction.update(doc_ref, updates)
        
        game_data.update(updates)
        return game_data, accumulated_income
        
    # Если дохода нет, просто обновляем время сбора, но не баланс
    updates = {"last_collection_time": new_time}
    transaction.update(doc_ref, updates)
    game_data.update(updates)
    return game_data, 0.0


@firestore.transactional
def buy_sector_transaction(transaction: Transaction, doc_ref, game_data: dict, sector_id: str) -> tuple[dict, bool, float]:
    """Покупает следующий уровень сектора в транзакции."""
    
    # Сначала собираем любой накопленный доход
    game_data, collected_amount = collect_income_transaction(transaction, doc_ref, game_data)

    current_level = game_data['sectors'].get(sector_id, 0)
    config = SECTORS_CONFIG.get(sector_id)

    if not config:
        return game_data, False, collected_amount
        
    # Расчет стоимости
    cost = config["base_cost"] * (current_level + 1)
    
    if game_data['balance'] >= cost:
        # Выполняем покупку
        new_balance = game_data['balance'] - cost
        new_level = current_level + 1
        
        # Обновление данных
        game_data['sectors'][sector_id] = new_level
        
        updates = {
            "balance": round(new_balance, 2),
            f"sectors.{sector_id}": new_level,
        }
        
        # Мы уже обновили last_collection_time через collect_income_transaction
        transaction.update(doc_ref, updates)
        
        game_data.update(updates)
        return game_data, True, collected_amount
    
    # Недостаточно средств
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


@app.post("/api/load_state", response_model=GameState)
async def load_state(user_id: str = Depends(get_auth_data)):
    """Загружает или создает состояние игры и рассчитывает доступный доход."""
    doc_ref = get_user_doc_ref(user_id)
    transaction = DB_CLIENT.transaction()
    
    try:
        game_data = get_or_create_state_transaction(transaction, doc_ref, user_id)
        
        # Рассчитываем доступный доход без сбора
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
        # 1. Сначала загружаем текущее состояние (без транзакции, чтобы избежать двойного вызова)
        current_data = doc_ref.get().to_dict()
        if not current_data:
             # Это должно быть обработано через load_state, но на всякий случай
            raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
            
        # 2. Выполняем сбор в транзакции
        updated_data, collected_amount = collect_income_transaction(transaction, doc_ref, current_data)
        
        # Обновляем поля Pydantic
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
        # 1. Сначала загружаем текущее состояние
        current_data = doc_ref.get().to_dict()
        if not current_data:
            raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
            
        # 2. Выполняем покупку в транзакции (включает сбор дохода)
        updated_data, success, collected_amount = buy_sector_transaction(transaction, doc_ref, current_data, sector_id)
        
        # Обновляем поля Pydantic
        updated_data['available_income'] = 0.0
        updated_data['purchase_successful'] = success
        updated_data['collected_amount'] = collected_amount
        
        if not success:
             # Возвращаем 400, но с обновленным состоянием (если баланс изменился)
             return GameState(**updated_data) # Должен быть 200, чтобы UI мог обновить баланс
        
        return GameState(**updated_data)
        
    except Exception as e:
        logger.error(f"Ошибка buy_sector для пользователя {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка покупки сектора.")
