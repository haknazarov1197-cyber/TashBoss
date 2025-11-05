import os
import sys
import json
import logging
import binascii
from base64 import b64decode
from datetime import datetime, timezone

# Starlette imports
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

# Firebase Admin SDK imports
import firebase_admin 
from firebase_admin import credentials, firestore, auth
from firebase_admin.exceptions import FirebaseError

# --- Настройки Логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("api")

# --- Константы Приложения ---
APP_ID = "tashboss-clicker-app" # Используется для пути Firestore и project ID
DB_CLIENT = None 
SECTORS_CONFIG = {
    "sector1": {"base_cost": 100, "passive_income": 0.5},
    "sector2": {"base_cost": 500, "passive_income": 2.0},
    "sector3": {"base_cost": 2500, "passive_income": 10.0},
}
INITIAL_BALANCE = 100.0

# --- Утилиты для Firebase ---

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ состояния пользователя в Firestore."""
    return DB_CLIENT.document(f'artifacts/{APP_ID}/users/{user_id}/tashboss_clicker/{user_id}')

def get_current_time():
    """Возвращает текущее время в UTC (для единообразия)."""
    return datetime.now(timezone.utc)

def add_padding_if_needed(data: str) -> str:
    """Исправляет Base64 padding для ключа Firebase."""
    data = data.strip().replace('"', '').replace("'", '')
    padding_needed = len(data) % 4
    if padding_needed != 0:
        data += '=' * (4 - padding_needed)
    return data

# --- Инициализация Firebase ---

def init_firebase():
    """Инициализирует Firebase Admin SDK и возвращает клиента Firestore."""
    logger.info("Попытка декодирования и инициализации Firebase...")
    
    firebase_key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")

    if not firebase_key:
        logger.critical("FIREBASE_SERVICE_ACCOUNT_KEY не установлен.")
        sys.exit(1)
        
    try:
        # 1. Исправление Base64 Padding
        padded_key = add_padding_if_needed(firebase_key)
        decoded_key_bytes = b64decode(padded_key)
        
        # 2. Загрузка JSON и инициализация
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db_client = firestore.client()
        
        logger.info("✅ Firebase успешно инициализирован.")
        return db_client
        
    except binascii.Error as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка Base64. Ключ поврежден: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации: {e}")
        sys.exit(1)

# --- Аутентификация ---

async def get_user_id(request: Request) -> str:
    """Извлекает и проверяет токен Firebase ID из заголовка Authorization."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Отсутствует или неверный заголовок авторизации")
    
    token = auth_header.split(" ")[1]
    
    try:
        # Проверка токена с помощью Firebase Admin SDK
        decoded_token = auth.verify_id_token(token)
        return decoded_token['uid']
    except FirebaseError as e:
        logger.error(f"Недействительный токен Firebase: {e}")
        raise HTTPException(status_code=401, detail="Недействительный токен аутентификации")
    except Exception as e:
        logger.error(f"Неожиданная ошибка аутентификации: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера при аутентификации")

# --- Логика Игры ---

def calculate_income(state: dict) -> tuple[float, dict]:
    """Рассчитывает пассивный доход, накопленный с last_collection_time."""
    last_time = state.get('last_collection_time')
    
    # Если время сбора не установлено, дохода нет
    if not last_time:
        return 0.0, state

    # Преобразуем Firestore Timestamp в Python datetime (UTC)
    if hasattr(last_time, 'astimezone'):
         last_time = last_time.astimezone(timezone.utc)
    else: # Обработка случаев, когда это может быть просто datetime (без timezone)
         last_time = last_time.replace(tzinfo=timezone.utc)


    time_delta = (get_current_time() - last_time).total_seconds()
    
    if time_delta <= 0:
        return 0.0, state

    total_income_per_second = sum(
        SECTORS_CONFIG[s_id]['passive_income'] * level
        for s_id, level in state.get('sectors', {}).items()
    )

    accumulated_income = total_income_per_second * time_delta
    
    return accumulated_income, state

# --- Маршруты API ---

@firestore.transactional
def get_state_or_initialize_transaction(transaction, doc_ref, user_id):
    """Транзакция для загрузки или инициализации состояния."""
    snapshot = doc_ref.get(transaction=transaction)
    
    if snapshot.exists:
        state = snapshot.to_dict()
        
        # 1. Рассчитываем пассивный доход перед возвратом
        accumulated_income, state = calculate_income(state)
        state['available_income'] = accumulated_income
        
        # 2. Обновляем время (чтобы избежать двойного начисления)
        # ВНИМАНИЕ: Мы не обновляем документ в транзакции при чтении, 
        # только рассчитываем доступный доход для UI.
        # Фактический сбор происходит в collect_income.
        
        return state
    else:
        # Инициализация нового состояния
        new_state = {
            'user_id': user_id,
            'balance': INITIAL_BALANCE,
            'sectors': {"sector1": 0, "sector2": 0, "sector3": 0},
            'last_collection_time': get_current_time(), # Время, с которого начнется пассивный доход
            'available_income': 0.0
        }
        transaction.set(doc_ref, new_state)
        return new_state

async def load_state(request: Request):
    """Загружает текущее состояние игры пользователя."""
    try:
        user_id = await get_user_id(request)
        doc_ref = get_user_doc_ref(user_id)
        
        # Выполняем транзакцию
        transaction = DB_CLIENT.transaction()
        state = get_state_or_initialize_transaction(transaction, doc_ref, user_id)
        
        # Удаляем объект Timestamp перед отправкой, оставляя только float/string
        state.pop('last_collection_time', None) 
        
        return JSONResponse(state)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Ошибка в load_state для {user_id}: {e}")
        return JSONResponse({"detail": "Ошибка при загрузке состояния игры."}, status_code=500)


@firestore.transactional
def collect_income_transaction(transaction, doc_ref):
    """Транзакция для сбора пассивного дохода."""
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Состояние игры не найдено.")
    
    state = snapshot.to_dict()
    
    # 1. Рассчитываем накопленный доход
    accumulated_income, state = calculate_income(state)
    
    # 2. Обновляем состояние
    state['balance'] += accumulated_income
    state['last_collection_time'] = get_current_time() # Сбрасываем таймер
    
    # 3. Сохраняем обратно в Firestore
    transaction.set(doc_ref, state)
    
    # Добавляем собранную сумму для ответа фронтенду
    state['collected_amount'] = accumulated_income
    
    # Удаляем объект Timestamp перед отправкой
    state.pop('last_collection_time', None) 

    return state

async def collect_income(request: Request):
    """Сбор пассивного дохода."""
    try:
        user_id = await get_user_id(request)
        doc_ref = get_user_doc_ref(user_id)
        
        transaction = DB_CLIENT.transaction()
        state = collect_income_transaction(transaction, doc_ref)
        
        return JSONResponse(state)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    except ValueError as e:
         return JSONResponse({"detail": str(e)}, status_code=404)
    except Exception as e:
        logger.error(f"Ошибка в collect_income для {user_id}: {e}")
        return JSONResponse({"detail": "Ошибка при сборе дохода."}, status_code=500)


@firestore.transactional
def buy_sector_transaction(transaction, doc_ref, sector_id: str):
    """Транзакция для покупки сектора."""
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Состояние игры не найдено.")
        
    state = snapshot.to_dict()
    
    # 1. Рассчитываем накопленный доход и сразу собираем его
    accumulated_income, state = calculate_income(state)
    state['balance'] += accumulated_income
    state['last_collection_time'] = get_current_time() # Сбрасываем таймер после сбора
    
    current_level = state['sectors'].get(sector_id, 0)
    sector_info = SECTORS_CONFIG.get(sector_id)

    if not sector_info:
        raise ValueError("Неверный ID сектора.")

    # Стоимость покупки следующего уровня
    cost = sector_info['base_cost'] * (current_level + 1)

    if state['balance'] < cost:
        # Возвращаем состояние с предварительно собранным доходом
        state.pop('last_collection_time', None) 
        state['collected_amount'] = accumulated_income
        return state
        
    # 2. Выполняем покупку
    state['balance'] -= cost
    state['sectors'][sector_id] = current_level + 1
    
    # 3. Сохраняем обратно в Firestore
    transaction.set(doc_ref, state)

    # Добавляем собранную сумму для ответа фронтенду
    state['collected_amount'] = accumulated_income
    
    # Удаляем объект Timestamp перед отправкой
    state.pop('last_collection_time', None) 

    return state

async def buy_sector(request: Request):
    """Покупка сектора."""
    try:
        user_id = await get_user_id(request)
        body = await request.json()
        sector_id = body.get('sector_id')
        
        if not sector_id:
            return JSONResponse({"detail": "sector_id отсутствует."}, status_code=400)
            
        doc_ref = get_user_doc_ref(user_id)
        
        transaction = DB_CLIENT.transaction()
        state = buy_sector_transaction(transaction, doc_ref, sector_id)

        # Проверка, прошла ли покупка, или вернулось состояние без изменений (недостаток средств)
        cost_of_attempted_purchase = SECTORS_CONFIG.get(sector_id)['base_cost'] * (state['sectors'].get(sector_id, 0))
        if state['balance'] + cost_of_attempted_purchase > SECTORS_CONFIG.get(sector_id)['base_cost'] * state['sectors'].get(sector_id, 0) and state['collected_amount'] > 0:
            # Покупка не состоялась из-за недостатка средств, но доход был собран.
            # Фронтенд должен проверить баланс, но мы здесь просто возвращаем состояние.
            pass
        
        return JSONResponse(state)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    except ValueError as e:
         return JSONResponse({"detail": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Ошибка в buy_sector для {user_id}: {e}")
        return JSONResponse({"detail": "Ошибка при покупке сектора."}, status_code=500)


# --- Настройка Starlette ---

# Обслуживание статических файлов
static_routes = [
    Mount("/", app=StaticFiles(directory="."), name="static"),
]

# Маршруты API
api_routes = [
    Route("/api/load_state", load_state, methods=["POST"]),
    Route("/api/collect_income", collect_income, methods=["POST"]),
    Route("/api/buy_sector", buy_sector, methods=["POST"]),
]

# Все маршруты приложения
app_routes = [
    # Static files should be checked last if using Starlette's routing system
    *api_routes,
    Route("/{path:path}", lambda r: JSONResponse({"detail": "Not Found"}), name="404")
]

# Middleware (CORS - Критически важно для WebApp)
middleware = [
    Middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
]

# --- События Жизненного Цикла ---

async def startup_event():
    global DB_CLIENT
    logger.info("Запуск функции Starlette startup_event...")
    # Инициализация Firebase при запуске
    DB_CLIENT = init_firebase()

# --- Создание Приложения ---

app = Starlette(
    debug=os.environ.get("DEBUG", "false").lower() == "true",
    # Мы используем Mount для обслуживания статики в конце, чтобы не мешать API
    routes=api_routes + [Mount("/", app=StaticFiles(directory=".", html=True), name="static_files")],
    middleware=middleware,
    on_startup=[startup_event]
)

# Для gunicorn, который использует `api:app`, это финальный объект приложения.
