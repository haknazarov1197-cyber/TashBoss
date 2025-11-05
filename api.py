import os
import sys
import json
import logging
from base64 import b64decode
from typing import Optional, Dict
from binascii import Error as Base64DecodeError
from datetime import datetime, timedelta

# FastAPI (Starlette) Dependencies
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

# Firebase Dependencies
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app, auth
# УДАЛЕНО: from google.cloud.firestore import transaction # -> Это вызывало ошибку ImportError
from google.cloud.firestore import transaction as firestore_transaction # Используем алиас, чтобы избежать конфликтов, если бы это было нужно, но в основном коде используется декоратор @firestore.transactional

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Глобальные переменные ---
TELEGRAM_BOT_TOKEN: Optional[str] = None
FIREBASE_SERVICE_ACCOUNT_KEY: Optional[str] = None
DB_CLIENT: Optional[firestore.client] = None
APP_ID = "tashboss-clicker-app" # Идентификатор приложения для Firestore
SECTORS = {
    'sector1': {'name': 'Сектор A', 'income': 1.0, 'cost': 100},
    'sector2': {'name': 'Сектор B', 'income': 5.0, 'cost': 500},
    'sector3': {'name': 'Сектор C', 'income': 20.0, 'cost': 2500},
}

# --- УТИЛИТА: ИСПРАВЛЕНИЕ BASE64 PADDING (С ДВОЙНОЙ ЗАЩИТОЙ) ---

def add_padding_if_needed(data: str) -> str:
    """
    Обеспечивает правильное заполнение данных Base64 для декодирования.
    Удаляет пробелы и добавляет '='.
    """
    # 1. Защита: Удаляем все пробелы, переносы строк и символы возврата каретки
    data = data.replace(' ', '').replace('\n', '').replace('\r', '') 
    
    # 2. Защита: Добавляем необходимое заполнение '='
    padding_needed = len(data) % 4
    if padding_needed != 0:
        data += '=' * (4 - padding_needed)
    return data

# --- Инициализация Firebase ---

def init_firebase() -> firestore.client:
    """Инициализирует Firebase Admin SDK."""
    global FIREBASE_SERVICE_ACCOUNT_KEY
    if firebase_admin._apps:
        return firestore.client()
        
    try:
        raw_key = FIREBASE_SERVICE_ACCOUNT_KEY
        
        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Применяем надежное заполнение
        padded_key = add_padding_if_needed(raw_key)
        
        logger.info("Попытка декодирования ключа Firebase...")

        # Декодируем и загружаем как JSON
        decoded_key_bytes = b64decode(padded_key)
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        
        # Инициализируем Firebase
        cred = credentials.Certificate(service_account_info)
        app = initialize_app(cred)
        logger.info("✅ Firebase успешно инициализирован.")
        
        return firestore.client(app)
            
    except (Base64DecodeError, json.JSONDecodeError, ValueError) as e:
        logger.critical(
            f"❌ КРИТИЧЕСКАЯ ОШИБКА FIREBASE: {type(e).__name__}: {e}. "
            "Ключ FIREBASE_SERVICE_ACCOUNT_KEY неверен или испорчен. "
            f"Длина ключа: {len(raw_key)} | Длина с padding: {len(padded_key)}"
        )
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Неизвестная ошибка при инициализации Firebase: {e}")
        sys.exit(1)


# --- Middlewares ---

# CORS Middleware (КРИТИЧЕСКИ ВАЖНО для WebApp)
middleware = [
    Middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
]


# --- Starlette Lifecycle Events ---

async def startup_event():
    """Вызывается при запуске приложения Starlette (Gunicorn Worker)."""
    global TELEGRAM_BOT_TOKEN, FIREBASE_SERVICE_ACCOUNT_KEY, DB_CLIENT

    logger.info("Запуск функции Starlette startup_event...")

    # 1. Загрузка переменных окружения
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    FIREBASE_SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")

    if not all([TELEGRAM_BOT_TOKEN, FIREBASE_SERVICE_ACCOUNT_KEY]):
        logger.critical(
            "❌ КРИТИЧЕСКАЯ ОШИБКА: Отсутствуют необходимые переменные окружения. "
            "Проверьте TELEGRAM_BOT_TOKEN и FIREBASE_SERVICE_ACCOUNT_KEY."
        )
        sys.exit(1)

    # 2. Инициализация Firebase
    DB_CLIENT = init_firebase() 
        
    logger.info("✅ Starlette startup_event завершен успешно.")


# --- УТИЛИТА: Аутентификация и данные пользователя ---

async def get_auth_data(request: Request) -> Dict:
    """Проверяет токен из заголовка Authorization и возвращает UID пользователя."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Отсутствует заголовок Authorization")

    token = auth_header.split(" ")[1]

    try:
        # Для простоты, используем токен (query_id) как user_id для демонстрации.
        return {"uid": token} 

    except Exception as e:
        logger.error(f"Ошибка проверки токена: {e}")
        raise HTTPException(status_code=401, detail="Недействительный токен")


def get_user_doc_ref(user_id: str) -> firestore.document.DocumentReference:
    """Возвращает ссылку на документ пользователя в Firestore."""
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return DB_CLIENT.document(f'artifacts/{APP_ID}/users/{user_id}/tashboss_clicker/{user_id}')


# --- API Логика Игры (Transaction Handlers) ---

def calculate_passive_income(sectors: Dict, last_collection_time: datetime) -> float:
    """Рассчитывает доход с момента последнего сбора."""
    now = datetime.now()
    time_delta = now - last_collection_time
    seconds = time_delta.total_seconds()
    
    total_income_per_second = sum(
        SECTORS.get(key, {}).get('income', 0.0) * count 
        for key, count in sectors.items()
    )
    
    # Максимальный доход за 24 часа, чтобы избежать огромных чисел при долгом отсутствии
    max_seconds = 24 * 3600 
    seconds = min(seconds, max_seconds)
    
    return seconds * total_income_per_second

def get_initial_state(user_id: str) -> Dict:
    """Возвращает начальное состояние игры."""
    return {
        "user_id": user_id,
        "balance": 100.0,
        "sectors": {"sector1": 1, "sector2": 0, "sector3": 0}, # Начинаем с 1 сектора 1-го уровня
        "last_collection_time": datetime.now().isoformat(),
        "total_income_per_second": SECTORS['sector1']['income'] * 1.0,
        "available_income": 0.0,
    }


@firestore.transactional
def load_state_transaction(transaction, user_doc_ref: firestore.document.DocumentReference, user_id: str) -> Dict:
    """Загружает или инициализирует состояние игры в транзакции."""
    
    snapshot = user_doc_ref.get(transaction=transaction)
    
    if snapshot.exists:
        data = snapshot.to_dict()
        last_collection_time = datetime.fromisoformat(data['last_collection_time'])
        
        # Рассчитываем доступный доход
        available_income = calculate_passive_income(data.get('sectors', {}), last_collection_time)
        
        # Обновляем состояние для фронтенда (без сохранения в БД)
        data['available_income'] = round(available_income, 2)
        data['total_income_per_second'] = sum(
            SECTORS.get(key, {}).get('income', 0.0) * count 
            for key, count in data.get('sectors', {}).items()
        )
        return data
        
    else:
        # Инициализируем новое состояние
        initial_state = get_initial_state(user_id)
        # Сохраняем начальное состояние
        transaction.set(user_doc_ref, initial_state)
        # Конвертируем datetime в float (для JSON response)
        initial_state['available_income'] = 0.0
        initial_state['total_income_per_second'] = SECTORS['sector1']['income'] * 1.0
        return initial_state


@firestore.transactional
def collect_income_transaction(transaction, user_doc_ref: firestore.document.DocumentReference) -> Dict:
    """Собирает пассивный доход и обновляет время сбора."""
    
    snapshot = user_doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Состояние игры не найдено. Начните с /load_state.")
        
    data = snapshot.to_dict()
    last_collection_time = datetime.fromisoformat(data['last_collection_time'])
    
    collected_amount = calculate_passive_income(data.get('sectors', {}), last_collection_time)
    
    if collected_amount > 0:
        # Округляем до 2 знаков для точности
        collected_amount = round(collected_amount, 2)
        new_balance = round(data.get('balance', 0.0) + collected_amount, 2)
        new_time = datetime.now()
        
        # Обновляем документ
        transaction.update(user_doc_ref, {
            'balance': new_balance,
            'last_collection_time': new_time.isoformat()
        })

        # Обновляем данные для ответа
        data['balance'] = new_balance
        data['last_collection_time'] = new_time.isoformat()
        data['available_income'] = 0.0 # После сбора доступный доход становится 0
        data['collected_amount'] = collected_amount
        return data
    else:
        # Ничего не собиралось
        data['collected_amount'] = 0.0
        data['available_income'] = 0.0
        return data


@firestore.transactional
def buy_sector_transaction(transaction, user_doc_ref: firestore.document.DocumentReference, sector_id: str) -> Dict:
    """Покупает следующий уровень сектора."""
    
    if sector_id not in SECTORS:
        raise ValueError("Неверный ID сектора.")

    snapshot = user_doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Состояние игры не найдено. Начните с /load_state.")
        
    data = snapshot.to_dict()
    current_sectors = data.get('sectors', {})
    
    # Расчет стоимости следующего уровня (простая прогрессия: cost * (уровень + 1))
    current_level = current_sectors.get(sector_id, 0)
    
    # Расчет стоимости
    base_cost = SECTORS[sector_id]['cost']
    cost_multiplier = current_level + 1
    next_cost = base_cost * cost_multiplier
    
    current_balance = data.get('balance', 0.0)
    
    if current_balance < next_cost:
        raise ValueError("Недостаточно BossCoin.")

    # 1. Рассчитываем и собираем весь доход перед покупкой (чтобы не потерять его)
    last_collection_time = datetime.fromisoformat(data['last_collection_time'])
    collected_amount = calculate_passive_income(current_sectors, last_collection_time)
    
    # 2. Обновляем баланс
    new_balance = round(current_balance + collected_amount - next_cost, 2)
    new_level = current_level + 1
    
    # 3. Обновляем секторы
    current_sectors[sector_id] = new_level
    
    # 4. Обновляем время сбора (так как мы его собрали)
    new_time = datetime.now()
    
    # 5. Обновляем документ в транзакции
    transaction.update(user_doc_ref, {
        'balance': new_balance,
        'sectors': current_sectors,
        'last_collection_time': new_time.isoformat(),
    })
    
    # Обновляем данные для ответа
    data['balance'] = new_balance
    data['sectors'] = current_sectors
    data['last_collection_time'] = new_time.isoformat()
    data['collected_amount'] = round(collected_amount, 2)
    data['available_income'] = 0.0
    return data


# --- Starlette Routes (HTTP Endpoints) ---

async def load_state_route(request: Request):
    """POST /api/load_state: Загружает или инициализирует состояние игры."""
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data["uid"]
        
        user_doc_ref = get_user_doc_ref(user_id)
        
        # Выполняем транзакцию
        snapshot = DB_CLIENT.transaction()
        state = load_state_transaction(snapshot, user_doc_ref, user_id)
        
        return JSONResponse(state)
        
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Ошибка load_state_route: {e}", exc_info=True)
        return JSONResponse({"detail": f"Ошибка сервера: {e.__class__.__name__}"}, status_code=500)

async def collect_income_route(request: Request):
    """POST /api/collect_income: Собирает пассивный доход."""
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data["uid"]
        
        user_doc_ref = get_user_doc_ref(user_id)
        
        snapshot = DB_CLIENT.transaction()
        state = collect_income_transaction(snapshot, user_doc_ref)
        
        return JSONResponse(state)

    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Ошибка collect_income_route: {e}", exc_info=True)
        return JSONResponse({"detail": f"Ошибка сервера: {e.__class__.__name__}"}, status_code=500)

async def buy_sector_route(request: Request):
    """POST /api/buy_sector: Покупает следующий уровень сектора."""
    try:
        auth_data = await get_auth_data(request)
        user_id = auth_data["uid"]
        
        body = await request.json()
        sector_id = body.get("sector_id")
        
        if not sector_id:
            raise HTTPException(status_code=400, detail="Не указан sector_id")
            
        user_doc_ref = get_user_doc_ref(user_id)
        
        snapshot = DB_CLIENT.transaction()
        state = buy_sector_transaction(snapshot, user_doc_ref, sector_id)
        
        return JSONResponse(state)
        
    except ValueError as e:
        # Ловим ошибки из транзакций (например, "Недостаточно BossCoin")
        return JSONResponse({"detail": str(e)}, status_code=400)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Ошибка buy_sector_route: {e}", exc_info=True)
        return JSONResponse({"detail": f"Ошибка сервера: {e.__class__.__name__}"}, status_code=500)


async def root_route(request: Request):
    """Обслуживает index.html для / и /webapp."""
    # Возвращаем index.html. Starlette StaticFiles уже настроен.
    # Фактически, нам просто нужно вернуть HTML.
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(html_content)


# --- Настройка Starlette Application ---

routes = [
    Route("/", endpoint=root_route), # Главная страница
    Route("/webapp", endpoint=root_route), # WebApp Endpoint
    Route("/api/load_state", endpoint=load_state_route, methods=["POST"]),
    Route("/api/collect_income", endpoint=collect_income_route, methods=["POST"]),
    Route("/api/buy_sector", endpoint=buy_sector_route, methods=["POST"]),
]

# Создаем приложение Starlette
app = Starlette(
    routes=routes,
    middleware=middleware,
    on_startup=[startup_event],
)
