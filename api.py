import os
import sys
import logging
import json
from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime, timezone
from typing import Dict, Any, Tuple

# FastAPI/Starlette imports
from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

# Firebase Admin SDK imports
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, auth, exceptions as firebase_exceptions
    # Используем декоратор для транзакций
    from firebase_admin._firestore_helpers import transactional
except ImportError:
    logging.critical("❌ CRITICAL ERROR: Library 'firebase-admin' not found. Please install it.")
    sys.exit(1)

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
    'sector1': {'cost': 100, 'income': 0.5},
    'sector2': {'cost': 500, 'income': 2.0},
    'sector3': {'cost': 2500, 'income': 10.0},
}

# --- CRITICAL BASE64 PADDING FIX ---
def add_padding_if_needed(data: str) -> str:
    """
    Ensures Base64 data is correctly padded for decoding.
    Environment variables often strip the necessary trailing '=' characters.
    """
    data = data.strip()
    padding_needed = len(data) % 4
    if padding_needed != 0:
        data += '=' * (4 - padding_needed)
    return data
# --- END BASE64 PADDING FIX ---

# --- Authentication and Utility Functions ---

class UnauthorizedException(Exception):
    """Custom exception for 401 Unauthorized errors."""
    pass

async def get_auth_data(request: Request) -> str:
    """
    Извлекает и верифицирует токен Firebase, возвращая UID пользователя.
    """
    auth_header = request.headers.get('authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        logger.warning("Missing or invalid Authorization header.")
        raise UnauthorizedException("Authorization header is missing or malformed.")

    id_token = auth_header.split(' ')[1]
    
    # В реальном приложении Mini App Telegram, токен, передаваемый здесь, 
    # является JWT, сгенерированным вашим бэкендом после верификации initData.
    # В этой среде мы предполагаем, что это ID Token Firebase.
    try:
        # ПРИМЕЧАНИЕ: В тестовой среде Canvas, где нет реального провайдера, 
        # этот метод может не работать, но он является обязательным для 
        # продакшн-окружения с Firebase.
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        return uid
    except firebase_exceptions.AuthError as e:
        logger.error(f"Firebase Auth Error: {e}")
        raise UnauthorizedException(f"Invalid authentication token: {e}")
    except Exception as e:
        logger.error(f"Unexpected authentication error: {e}")
        raise UnauthorizedException(f"Authentication failed: {e}")


def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db.collection('artifacts').document(APP_ID).collection('users').document(user_id).collection('tashboss_clicker').document(user_id)


def get_current_time_str() -> str:
    """Возвращает текущее время в формате ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


# --- Game Logic Functions ---

def calculate_passive_income(state: Dict[str, Any]) -> Tuple[float, datetime]:
    """Рассчитывает доход с момента последнего сбора."""
    
    # Если состояние инициализировано без времени сбора
    if not state or 'last_collection_time' not in state:
        return 0.0, datetime.now(timezone.utc)

    try:
        last_time = datetime.fromisoformat(state['last_collection_time'])
    except ValueError:
        logger.error("Invalid last_collection_time format. Re-initializing time.")
        last_time = datetime.now(timezone.utc)
    
    current_time = datetime.now(timezone.utc)
    
    time_delta_seconds = (current_time - last_time).total_seconds()
    
    total_income_per_second = sum(
        SECTORS_CONFIG[key]['income'] * state['sectors'].get(key, 0)
        for key in SECTORS_CONFIG
    )
    
    income = time_delta_seconds * total_income_per_second
    
    # Лимит времени: не собирать доход более чем за 7 дней (чтобы избежать переполнения)
    MAX_COLLECTION_SECONDS = 7 * 24 * 60 * 60
    if time_delta_seconds > MAX_COLLECTION_SECONDS:
        income = MAX_COLLECTION_SECONDS * total_income_per_second
        # Устанавливаем новое время сбора как (текущее время - лимит)
        new_last_time = current_time - (current_time - last_time).replace(seconds=MAX_COLLECTION_SECONDS)
    else:
        new_last_time = current_time
    
    return max(0.0, income), new_last_time


# --- API Endpoints Handlers ---

async def handle_api_request(request: Request, endpoint_handler):
    """Общий обработчик для аутентификации и обработки исключений API."""
    try:
        user_id = await get_auth_data(request)
        return await endpoint_handler(request, user_id)
    except UnauthorizedException as e:
        return JSONResponse({"detail": str(e)}, status_code=401)
    except Exception as e:
        logger.error(f"API Error in {request.url.path}: {e}")
        return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)


@transactional
def get_or_create_state_transaction(transaction, doc_ref, user_id: str):
    """
    Транзакция для загрузки или создания состояния игры.
    """
    try:
        snapshot = doc_ref.get(transaction=transaction)
    except Exception as e:
        logger.error(f"Firestore read error during transaction: {e}")
        raise

    if snapshot.exists:
        state = snapshot.to_dict()
    else:
        # Начальное состояние
        initial_state = {
            'user_id': user_id,
            'balance': 100.0,
            'sectors': {'sector1': 0, 'sector2': 0, 'sector3': 0},
            'last_collection_time': get_current_time_str()
        }
        transaction.set(doc_ref, initial_state)
        state = initial_state
        logger.info(f"Created new game state for user: {user_id}")
    
    return state


async def load_state_endpoint(request: Request, user_id: str):
    """POST /api/load_state: Загружает или инициализирует состояние игры."""
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        state = get_or_create_state_transaction(db.transaction(), doc_ref, user_id)
        
        # Рассчитываем и добавляем доступный доход для отображения на клиенте
        income, _ = calculate_passive_income(state)
        state['available_income'] = income
        
        return JSONResponse(state)
    except Exception as e:
        logger.error(f"Error loading state for user {user_id}: {e}")
        return JSONResponse({"detail": "Не удалось загрузить состояние игры"}, status_code=500)


@transactional
def collect_income_transaction(transaction, doc_ref, state: Dict[str, Any]):
    """
    Транзакция для сбора пассивного дохода.
    """
    try:
        # Пересчитываем доход
        income, new_last_time = calculate_passive_income(state)
        
        if income > 0:
            new_balance = state['balance'] + income
            
            # Обновление в транзакции
            transaction.update(doc_ref, {
                'balance': new_balance,
                'last_collection_time': new_last_time.isoformat()
            })
            
            # Обновляем локальное состояние для возврата
            state['balance'] = new_balance
            state['last_collection_time'] = new_last_time.isoformat()
            state['collected_amount'] = income
        else:
            state['collected_amount'] = 0.0

        return state
        
    except Exception as e:
        logger.error(f"Firestore transaction error (collect_income): {e}")
        raise


async def collect_income_endpoint(request: Request, user_id: str):
    """POST /api/collect_income: Сбор пассивного дохода."""
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        state = get_or_create_state_transaction(db.transaction(), doc_ref, user_id)
        updated_state = collect_income_transaction(db.transaction(), doc_ref, state)
        return JSONResponse(updated_state)
    except Exception as e:
        logger.error(f"Error collecting income for user {user_id}: {e}")
        return JSONResponse({"detail": "Не удалось собрать доход"}, status_code=500)


@transactional
def buy_sector_transaction(transaction, doc_ref, state: Dict[str, Any], sector_id: str):
    """
    Транзакция для покупки сектора.
    """
    config = SECTORS_CONFIG.get(sector_id)
    if not config:
        raise ValueError("Invalid sector ID.")

    cost = config['cost']
    
    if state['balance'] < cost:
        raise ValueError("Insufficient balance.")
        
    # Рассчитываем и собираем доступный доход перед покупкой
    income, collection_time_after_income = calculate_passive_income(state)
    new_balance = state['balance'] + income - cost
    
    # Увеличиваем уровень сектора
    new_sectors = state['sectors']
    new_sectors[sector_id] = new_sectors.get(sector_id, 0) + 1
    
    # Обновление в транзакции
    transaction.update(doc_ref, {
        'balance': new_balance,
        'sectors': new_sectors,
        'last_collection_time': collection_time_after_income.isoformat()
    })
    
    # Обновляем локальное состояние для возврата
    state['balance'] = new_balance
    state['sectors'] = new_sectors
    state['last_collection_time'] = collection_time_after_income.isoformat()
    state['purchased_sector'] = sector_id

    return state


async def buy_sector_endpoint(request: Request, user_id: str):
    """POST /api/buy_sector: Покупка сектора."""
    doc_ref = get_user_doc_ref(user_id)
    try:
        data = await request.json()
        sector_id = data.get('sector_id')
        
        if sector_id not in SECTORS_CONFIG:
            return JSONResponse({"detail": "Неверный идентификатор сектора"}, status_code=400)
            
        state = get_or_create_state_transaction(db.transaction(), doc_ref, user_id)
        updated_state = buy_sector_transaction(db.transaction(), doc_ref, state, sector_id)
        
        return JSONResponse(updated_state)
    
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Error buying sector for user {user_id}: {e}")
        return JSONResponse({"detail": "Не удалось купить сектор"}, status_code=500)


# --- Application Setup ---

async def startup_event():
    """Событие запуска приложения: инициализация Firebase и Firestore."""
    global db, FIREBASE_INITIALIZED
    logger.info("⚡️ Starting up and initializing Firebase...")

    # Получение Base64 ключа
    FIREBASE_KEY_BASE64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    
    if not FIREBASE_KEY_BASE64:
        logger.critical("❌ CRITICAL ERROR: Environment variable FIREBASE_SERVICE_ACCOUNT_KEY is not set.")
        sys.exit(1)

    try:
        # 1. ПРИМЕНЕНИЕ КРИТИЧЕСКОГО ИСПРАВЛЕНИЯ BASE64 PADDING
        padded_key = add_padding_if_needed(FIREBASE_KEY_BASE64)

        # 2. Декодирование исправленного ключа
        decoded_key_bytes = b64decode(padded_key)
        
        # 3. Загрузка JSON-объекта
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        
        # 4. Инициализация Firebase
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        
        FIREBASE_INITIALIZED = True
        logger.info("✅ Firebase successfully initialized and Firestore client created.")
        
    except BinasciiError as e:
        logger.critical(f"❌ CRITICAL ERROR: Failed to initialize Firebase. Base64 decoding error (Incorrect padding): {e}. Check the key.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ CRITICAL ERROR: Unexpected error during Firebase initialization: {e}")
        sys.exit(1)


async def homepage_handler(request):
    """Обработчик для корневого маршрута и /webapp, возвращающий index.html."""
    return FileResponse('index.html')


# Настройка маршрутов
routes = [
    # API endpoints
    Route("/api/load_state", lambda r: handle_api_request(r, load_state_endpoint), methods=["POST"]),
    Route("/api/collect_income", lambda r: handle_api_request(r, collect_income_endpoint), methods=["POST"]),
    Route("/api/buy_sector", lambda r: handle_api_request(r, buy_sector_endpoint), methods=["POST"]),
    # Serve index.html for the main and webapp routes
    Route("/", homepage_handler),
    Route("/webapp", homepage_handler),
    # Mount StaticFiles for serving app.js and other assets (if any)
    Mount("/", app=StaticFiles(directory="."), name="static"),
]

# Настройка middleware (CORS КРИТИЧЕСКИ ВАЖЕН)
middleware = [
    Middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*']),
]

# Инициализация Starlette Application
app = Starlette(
    routes=routes,
    middleware=middleware,
    on_startup=[startup_event],
)
