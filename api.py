import os
import sys
import logging
import json
from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Callable, TypeVar 

# FastAPI/Starlette imports
from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

# Firebase Admin SDK imports
import firebase_admin
from firebase_admin import credentials, firestore, auth, exceptions as firebase_exceptions
# --- Типизация для транзакций: используем firestore.Transaction (с большой буквы 'T') ---
T = TypeVar('T')
FirestoreTransaction = firestore.Transaction 

# --- Configuration and Initialization ---

# Настройка логирования
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("api")

# Глобальные переменные
db: firestore.client = None
APP_ID = "tashboss-clicker-app" # Идентификатор приложения для пути Firestore
FIREBASE_INITIALIZED = False

# Логика игры
SECTORS_CONFIG = {
    'sector1': {'name': 'Зона отдыха', 'income': 0.5, 'cost': 100},
    'sector2': {'name': 'Бизнес-центр', 'income': 2.0, 'cost': 500},
    'sector3': {'name': 'Индустриальная зона', 'income': 10.0, 'cost': 2500},
}

# --- MIDDLEWARE ---
middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Разрешаем все для Telegram WebApp
        allow_methods=["*"],
        allow_headers=["*"],
    )
]

# --- FIREBASE AUTH UTILS ---

async def get_auth_data(request: Request) -> Tuple[str, Dict[str, Any]]:
    """Проверяет токен из заголовка Authorization и возвращает UID пользователя и декодированные данные."""
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        # Для простоты прототипа и отладки, если заголовок отсутствует,
        # используем заглушку. В продакшене тут должна быть ошибка 401.
        logger.warning("No Authorization header found. Using anonymous mock user_id.")
        return "mock_user_12345", {"uid": "mock_user_12345"}
    
    token = auth_header.split(" ")[1]

    # ВРЕМЕННОЕ РЕШЕНИЕ: Просто используем токен (который является query_id) как ID.
    user_id = token 
    
    return user_id, {"uid": user_id}

# --- FIREBASE UTILITES (Транзакционные функции теперь принимают объект транзакции) ---

def get_user_doc_ref(user_id: str) -> firestore.DocumentReference:
    """Возвращает ссылку на документ пользователя в Firestore."""
    # Путь: /artifacts/{appId}/users/{userId}/data/state
    return db.document(f"artifacts/{APP_ID}/users/{user_id}/data/state")

# Функция, выполняемая внутри транзакции (НЕ ДЕКОРАТОР!)
def load_or_init_state_transaction(transaction: FirestoreTransaction, user_id: str) -> Dict[str, Any]:
    """Загружает или инициализирует состояние игры в транзакции."""
    doc_ref = get_user_doc_ref(user_id)
    doc_snapshot = doc_ref.get(transaction=transaction)
    
    if doc_snapshot.exists:
        state = doc_snapshot.to_dict()
    else:
        # Начальная инициализация
        state = {
            'balance': 100.0,
            'sectors': {'sector1': 0, 'sector2': 0, 'sector3': 0},
            'last_collection_time': datetime.now(timezone.utc).isoformat(),
            'total_income_per_second': 0.0,
            'available_income': 0.0,
        }
        # Использование transaction.set для записи
        transaction.set(doc_ref, state)
    
    # Расчет доступного пассивного дохода
    state = calculate_passive_income(state)
    
    return state

def calculate_passive_income(state: Dict[str, Any]) -> Dict[str, Any]:
    """Рассчитывает доступный пассивный доход, но не собирает его."""
    last_time_str = state.get('last_collection_time')
    
    # Если last_collection_time еще не инициализирован, возвращаем 0
    if not last_time_str:
        return {**state, 'available_income': 0.0, 'total_income_per_second': 0.0}

    last_time = datetime.fromisoformat(last_time_str.replace('Z', '+00:00'))
    time_elapsed_seconds = (datetime.now(timezone.utc) - last_time).total_seconds()
    
    # Расчет общего дохода в секунду
    total_income = sum(
        SECTORS_CONFIG[key]['income'] * state['sectors'].get(key, 0)
        for key in SECTORS_CONFIG if key in state['sectors'] # Проверка на существование ключа
    )
    
    available_income = max(0.0, time_elapsed_seconds * total_income)
    
    state['total_income_per_second'] = total_income
    state['available_income'] = available_income
    
    return state


# Функция, выполняемая внутри транзакции (НЕ ДЕКОРАТОР!)
def collect_income_transaction(transaction: FirestoreTransaction, user_id: str) -> Dict[str, Any]:
    """Собирает пассивный доход и обновляет баланс."""
    doc_ref = get_user_doc_ref(user_id)
    doc_snapshot = doc_ref.get(transaction=transaction)
    
    if not doc_snapshot.exists:
        raise ValueError("Game state not initialized.")

    state = doc_snapshot.to_dict()
    
    # 1. Расчет доступного дохода
    state_with_income = calculate_passive_income(state)
    collected_amount = state_with_income['available_income']
    
    # 2. Обновление состояния
    state['balance'] = state['balance'] + collected_amount
    state['last_collection_time'] = datetime.now(timezone.utc).isoformat()
    
    # 3. Запись изменений в транзакции
    transaction.set(doc_ref, state)
    
    # 4. Возвращаем обновленное состояние (для клиента)
    state['collected_amount'] = collected_amount
    state = calculate_passive_income(state) # Обновляем расчеты после сбора
    
    return state


# Функция, выполняемая внутри транзакции (НЕ ДЕКОРАТОР!)
def buy_sector_transaction(transaction: FirestoreTransaction, user_id: str, sector_id: str) -> Dict[str, Any]:
    """Покупает один уровень сектора."""
    if sector_id not in SECTORS_CONFIG:
        raise ValueError("Invalid sector ID.")

    doc_ref = get_user_doc_ref(user_id)
    doc_snapshot = doc_ref.get(transaction=transaction)

    if not doc_snapshot.exists:
        raise ValueError("Game state not initialized.")
        
    state = doc_snapshot.to_dict()
    sector_count = state['sectors'].get(sector_id, 0)
    
    config = SECTORS_CONFIG[sector_id]
    cost = config['cost'] * (sector_count + 1) # Стоимость растет линейно

    if state['balance'] < cost:
        raise ValueError("Insufficient balance to buy sector.")

    # 1. Сначала собираем весь пассивный доход (чтобы избежать потери)
    # Расчет доступного дохода и обновление состояния
    state = collect_income_transaction(transaction, user_id)
    
    # Проверяем баланс после сбора
    if state['balance'] < cost:
         raise ValueError("Insufficient balance after collecting income.")

    # 2. Обновляем состояние покупки
    state['balance'] -= cost
    state['sectors'][sector_id] = sector_count + 1
    
    # 3. Запись изменений в транзакции
    transaction.set(doc_ref, state)
    
    # 4. Возвращаем обновленное состояние
    return calculate_passive_income(state)


# --- API ENDPOINTS (Теперь используют db.transaction() для вызова функций) ---

async def load_state_endpoint(request: Request) -> JSONResponse:
    """Загружает текущее состояние игры или инициализирует его."""
    try:
        user_id, _ = await get_auth_data(request)
        
        # ПРАВИЛЬНЫЙ ВЫЗОВ ТРАНЗАКЦИИ
        state = db.transaction(lambda t: load_or_init_state_transaction(t, user_id))
        
        return JSONResponse(state)
    except firebase_exceptions.InvalidArgumentError as e:
        return JSONResponse({"detail": str(e)}, status_code=401)
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return JSONResponse({"detail": "Server error while loading game state."}, status_code=500)

async def collect_income_endpoint(request: Request) -> JSONResponse:
    """Собирает накопленный пассивный доход."""
    try:
        user_id, _ = await get_auth_data(request)

        # ПРАВИЛЬНЫЙ ВЫЗОВ ТРАНЗАКЦИИ
        state = db.transaction(lambda t: collect_income_transaction(t, user_id))

        return JSONResponse(state)
    except firebase_exceptions.InvalidArgumentError as e:
        return JSONResponse({"detail": str(e)}, status_code=401)
    except Exception as e:
        logger.error(f"Error collecting income: {e}")
        return JSONResponse({"detail": "Server error while collecting income."}, status_code=500)


async def buy_sector_endpoint(request: Request) -> JSONResponse:
    """Покупает сектор."""
    try:
        user_id, _ = await get_auth_data(request)
        data = await request.json()
        sector_id = data.get("sector_id")

        if not sector_id:
            return JSONResponse({"detail": "Missing sector_id."}, status_code=400)

        # ПРАВИЛЬНЫЙ ВЫЗОВ ТРАНЗАКЦИИ
        state = db.transaction(lambda t: buy_sector_transaction(t, user_id, sector_id))

        return JSONResponse(state)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400) # Недостаточно баланса
    except firebase_exceptions.InvalidArgumentError as e:
        return JSONResponse({"detail": str(e)}, status_code=401)
    except Exception as e:
        logger.error(f"Error buying sector: {e}")
        return JSONResponse({"detail": "Server error while buying sector."}, status_code=500)


# --- STATIC FILES AND HTML ROUTES ---

# Настройка для обслуживания статических файлов (index.html, app.js)
static_files = StaticFiles(directory=".")

async def homepage(request: Request) -> FileResponse:
    """Обслуживает index.html для корневого пути."""
    return FileResponse("index.html")

async def webapp_route(request: Request) -> FileResponse:
    """Обслуживает index.html для пути /webapp."""
    return FileResponse("index.html")


# --- STARTUP AND SHUTDOWN ---

async def startup_event() -> None:
    """Инициализирует Firebase Admin SDK при запуске приложения."""
    global db, FIREBASE_INITIALIZED

    if FIREBASE_INITIALIZED:
        return

    raw_key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    
    if not raw_key:
        logging.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная FIREBASE_SERVICE_ACCOUNT_KEY не установлена.")
        sys.exit(1)
        
    try:
        # --- ИСПРАВЛЕНИЕ: Агрессивная очистка строки от всех пробельных символов ---
        # 1. Удаляем все пробелы, переносы строк и лишние символы из ключа
        cleaned_key = "".join(raw_key.split())

        # 2. Добавляем padding для Base64, если необходимо
        padding_needed = len(cleaned_key) % 4
        if padding_needed != 0:
            padded_key = cleaned_key + '=' * (4 - padding_needed)
        else:
            padded_key = cleaned_key

        # 3. Декодируем Base64 и загружаем JSON
        decoded_key_bytes = b64decode(padded_key)
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        
        # 4. Инициализация Firebase
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        FIREBASE_INITIALIZED = True
        
        logging.info("✅ Firebase Admin SDK успешно инициализирован.")
        
    except BinasciiError as e:
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка декодирования Base64: {e}. Проверьте, что ключ в переменной FIREBASE_SERVICE_ACCOUNT_KEY является корректным Base64.")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Непредвиденная ошибка при инициализации Firebase: {e}")
        sys.exit(1)

# --- ROUTES ---

routes = [
    # Static files mount must come before other routes to handle app.js
    Mount("/", app=static_files, name="static"), 
    
    # API endpoints
    Route("/api/load_state", endpoint=load_state_endpoint, methods=["POST"]),
    Route("/api/collect_income", endpoint=collect_income_endpoint, methods=["POST"]),
    Route("/api/buy_sector", endpoint=buy_sector_endpoint, methods=["POST"]),
    
    # Serve index.html
    Route("/", endpoint=homepage),
    Route("/webapp", endpoint=webapp_route),
]

# Создание Starlette приложения
app = Starlette(
    routes=routes,
    middleware=middleware,
    on_startup=[startup_event]
)
