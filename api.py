import os
import sys
import json
import base64
import binascii
import logging
from datetime import datetime, timedelta, timezone

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, PlainTextResponse, HTMLResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
# from starlette.staticfiles import StaticFiles # Больше не нужен для app.js

from firebase_admin import credentials, initialize_app, firestore, auth
from google.cloud.firestore import transactional, Client as FirestoreClient

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("api")

# --- Глобальные Настройки ---
DB_CLIENT: FirestoreClient = None
APP_ID = "tashboss_clicker_app" # Используется для пути Firestore

# Конфигурация игры (должна совпадать с frontend)
SECTORS_CONFIG = {
    "sector1": {"name": "Сектор А", "passive_income": 0.5, "base_cost": 100},
    "sector2": {"name": "Сектор B", "passive_income": 2.0, "base_cost": 500},
    "sector3": {"name": "Сектор C", "passive_income": 10.0, "base_cost": 2500},
}
# Устанавливаем часовой пояс UTC для корректных расчетов времени
UTC_TZ = timezone.utc

# --- Вспомогательная функция для Base64 Padding (для надежной загрузки ключа) ---
def add_padding_if_needed(data: str) -> str:
    data = data.strip()
    padding_needed = len(data) % 4
    if padding_needed != 0:
        data += '=' * (4 - padding_needed)
    return data

# --- Инициализация Firebase ---
def init_firebase():
    """Инициализирует Firebase Admin SDK."""
    global DB_CLIENT
    FIREBASE_KEY_VAR = "FIREBASE_SERVICE_ACCOUNT_KEY" 
    
    logger.info("Попытка декодирования и инициализации Firebase...")
    key_base64_string = os.getenv(FIREBASE_KEY_VAR)
    
    if not key_base64_string:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{FIREBASE_KEY_VAR}' не найдена. Завершение работы.")
        sys.exit(1)
    
    try:
        # Применяем исправление padding
        padded_key = add_padding_if_needed(key_base64_string)
        decoded_key_bytes = base64.b64decode(padded_key)
        
    except (binascii.Error, TypeError) as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка Base64 декодирования. Ключ поврежден.")
        logger.error(f"Ошибка: {e}")
        sys.exit(1)
        
    try:
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка декодирования JSON/UTF-8. Ключ не является корректным JSON.")
        logger.error(f"Ошибка: {e}")
        sys.exit(1)

    try:
        cred = credentials.Certificate(service_account_info)
        initialize_app(cred)
        DB_CLIENT = firestore.client()
        logger.info("✅ Firebase успешно инициализирован.")
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации Firebase Admin SDK: {e}")
        sys.exit(1)

# --- Аутентификация и Авторизация ---
async def get_auth_data(request: Request):
    """Извлекает и верифицирует Firebase ID Token из заголовка Authorization."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="Отсутствует заголовок авторизации 'Bearer'.")

    id_token = auth_header.split(' ')[1]
    
    try:
        # Верификация токена и получение UID
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token['uid']
    except Exception as e:
        logger.warning(f"Ошибка верификации ID токена: {e}")
        raise HTTPException(status_code=401, detail="Неверный или просроченный токен Firebase ID.")

# Класс-заглушка для обработки HTTPException в Starlette
class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail

# --- API Эндпоинты ---

async def handle_http_exception(request, exc):
    """Обработчик для HTTPException."""
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


async def auth_token_endpoint(request: Request):
    """
    Эндпоинт для обмена Telegram User ID на Firebase Custom Token. 
    Используется для начального входа в index.html.
    """
    try:
        data = await request.json()
        telegram_user_id = data.get("telegram_user_id")
        
        if not telegram_user_id:
            return JSONResponse({"detail": "Требуется 'telegram_user_id'."}, status_code=400)

        # Создаем или получаем пользователя Firebase
        try:
            user = auth.get_user(telegram_user_id)
        except auth.AuthError:
            # Если пользователь не существует, создаем его
            user = auth.create_user(uid=telegram_user_id)
            logger.info(f"Создан новый пользователь Firebase с UID: {telegram_user_id}")

        # Генерируем Custom Token
        custom_token = auth.create_custom_token(telegram_user_id)
        
        # Base64-кодирование токена, так как он возвращается в виде байтов
        return JSONResponse({"token": custom_token.decode('utf-8')})
    
    except Exception as e:
        logger.error(f"Ошибка в auth_token_endpoint: {e}")
        return JSONResponse({"detail": "Ошибка сервера при генерации токена."}, status_code=500)


def calculate_passive_income(game_state):
    """Рассчитывает доход с момента last_collection_time."""
    
    last_collection = game_state.get('last_collection_time')
    
    # Конвертируем Firestore Timestamp в datetime с UTC
    if last_collection and last_collection.tzinfo is None:
        last_collection = last_collection.replace(tzinfo=UTC_TZ)
        
    time_since_last = datetime.now(UTC_TZ) - last_collection
    
    # Ограничиваем максимальное время сбора до 24 часов, чтобы избежать переполнения
    if time_since_last > timedelta(hours=24):
        time_since_last = timedelta(hours=24)

    total_seconds = time_since_last.total_seconds()
    
    total_income_per_second = 0.0
    for sector_id, level in game_state.get('sectors', {}).items():
        config = SECTORS_CONFIG.get(sector_id)
        if config:
            total_income_per_second += config['passive_income'] * level
            
    collected_amount = total_income_per_second * total_seconds
    
    # Обновляем доступный доход
    game_state['available_income'] += collected_amount
    
    return game_state, collected_amount


# --- Транзакционные Функции Firestore ---

def get_user_doc_ref(user_id):
    """Возвращает ссылку на документ пользователя."""
    if not DB_CLIENT:
        raise Exception("DB_CLIENT не инициализирован.")
        
    return DB_CLIENT.document(
        f"artifacts/{APP_ID}/users/{user_id}/tashboss_clicker/{user_id}"
    )

@firestore.transactional
def load_state_transaction(transaction, user_id):
    """Загружает или инициализирует состояние игры в транзакции."""
    doc_ref = get_user_doc_ref(user_id)
    snapshot = doc_ref.get(transaction=transaction)

    if snapshot.exists:
        state = snapshot.to_dict()
        # Рассчитываем и добавляем доступный доход при загрузке
        state, _ = calculate_passive_income(state)
    else:
        # Инициализация нового состояния
        state = {
            'user_id': user_id,
            'balance': 100.0,
            'sectors': {"sector1": 0, "sector2": 0, "sector3": 0},
            'last_collection_time': datetime.now(UTC_TZ),
            'available_income': 0.0
        }
        # Создаем документ
        transaction.set(doc_ref, state) 

    return state

@firestore.transactional
def collect_income_transaction(transaction, user_id):
    """Собирает доход и обновляет баланс/время в транзакции."""
    doc_ref = get_user_doc_ref(user_id)
    snapshot = doc_ref.get(transaction=transaction)
    
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
        
    state = snapshot.to_dict()
    
    # 1. Сначала рассчитываем накопленный доход
    state, accrued_income = calculate_passive_income(state)
    
    # 2. Переводим доступный доход на баланс
    collected_amount = state['available_income']
    state['balance'] += collected_amount
    state['available_income'] = 0.0
    
    # 3. Обновляем время сбора (это сбрасывает таймер для нового начисления)
    state['last_collection_time'] = datetime.now(UTC_TZ)
    
    # 4. Обновляем документ
    transaction.set(doc_ref, state)
    
    # Возвращаем обновленное состояние и собранную сумму для фронтенда
    return state, collected_amount

@firestore.transactional
def buy_sector_transaction(transaction, user_id, sector_id):
    """Покупает сектор в транзакции."""
    doc_ref = get_user_doc_ref(user_id)
    snapshot = doc_ref.get(transaction=transaction)
    
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Состояние игры не найдено.")

    config = SECTORS_CONFIG.get(sector_id)
    if not config:
        raise HTTPException(status_code=400, detail="Неверный ID сектора.")

    state = snapshot.to_dict()
    current_level = state['sectors'].get(sector_id, 0)
    
    # 1. Рассчитываем стоимость следующего уровня
    next_cost = config['base_cost'] * (current_level + 1)

    # 2. Проверяем баланс
    if state['balance'] < next_cost:
        # Возвращаем ошибку, но сначала собираем доход (как в требовании)
        state, collected_amount = collect_income_transaction(transaction, user_id)
        state['collected_amount'] = collected_amount
        state['purchase_successful'] = False
        return state

    # 3. Собираем пассивный доход перед покупкой (в той же транзакции)
    # Чтобы избежать двойного списания баланса, мы не вызываем collect_income_transaction, 
    # а делаем это вручную внутри текущей транзакции.
    state, collected_amount = calculate_passive_income(state)
    state['balance'] += state['available_income']
    state['available_income'] = 0.0
    state['last_collection_time'] = datetime.now(UTC_TZ)
    
    # 4. Выполняем покупку
    state['balance'] -= next_cost
    state['sectors'][sector_id] = current_level + 1
    
    # 5. Обновляем документ
    transaction.set(doc_ref, state)
    
    # Добавляем данные о сборе и успехе для фронтенда
    state['collected_amount'] = collected_amount
    state['purchase_successful'] = True
    
    return state


# --- API Маршруты (общие функции-обертки) ---

async def load_state_endpoint(request: Request):
    try:
        user_id = await get_auth_data(request)
        transaction = DB_CLIENT.transaction()
        state = load_state_transaction(transaction, user_id)
        
        # Поскольку Timestamp (last_collection_time) не JSON-сериализуем, 
        # конвертируем его в строку ISO для фронтенда.
        if isinstance(state.get('last_collection_time'), datetime):
             state['last_collection_time'] = state['last_collection_time'].isoformat()
             
        return JSONResponse(state)
        
    except HTTPException as e:
        raise e # Обрабатывается в middleware
    except Exception as e:
        logger.error(f"Ошибка load_state_endpoint: {e}")
        return JSONResponse({"detail": "Ошибка сервера при загрузке состояния."}, status_code=500)


async def collect_income_endpoint(request: Request):
    try:
        user_id = await get_auth_data(request)
        transaction = DB_CLIENT.transaction()
        
        # Вызываем транзакционную функцию
        state, collected_amount = collect_income_transaction(transaction, user_id)
        
        # Конвертация Timestamp
        if isinstance(state.get('last_collection_time'), datetime):
             state['last_collection_time'] = state['last_collection_time'].isoformat()
             
        # Добавляем собранную сумму в ответ для UI
        state['collected_amount'] = collected_amount
             
        return JSONResponse(state)
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка collect_income_endpoint: {e}")
        return JSONResponse({"detail": "Ошибка сервера при сборе дохода."}, status_code=500)


async def buy_sector_endpoint(request: Request):
    try:
        user_id = await get_auth_data(request)
        data = await request.json()
        sector_id = data.get("sector_id")

        if not sector_id:
            raise HTTPException(status_code=400, detail="Требуется 'sector_id'.")
            
        transaction = DB_CLIENT.transaction()
        state = buy_sector_transaction(transaction, user_id, sector_id)

        # Конвертация Timestamp
        if isinstance(state.get('last_collection_time'), datetime):
             state['last_collection_time'] = state['last_collection_time'].isoformat()
             
        return JSONResponse(state)
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка buy_sector_endpoint: {e}")
        return JSONResponse({"detail": "Ошибка сервера при покупке сектора."}, status_code=500)


# --- Основная Логика Starlette ---

async def startup_event():
    """Запускается при старте приложения."""
    logger.info("Запуск функции Starlette startup_event...")
    init_firebase() 

async def homepage(request):
    """Возвращает index.html для / и /webapp."""
    # Читаем файл index.html (предполагаем, что он в корне)
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content)
    except FileNotFoundError:
        return PlainTextResponse("index.html не найден.", status_code=500)
        
async def app_js_endpoint(request: Request):
    """Обслуживает файл app.js, чтобы избежать ошибки импорта в StaticFiles."""
    try:
        with open("app.js", "r", encoding="utf-8") as f:
            content = f.read()
        # Важно: устанавливаем правильный MIME-тип
        return PlainTextResponse(content, media_type="application/javascript")
    except FileNotFoundError:
        return PlainTextResponse("app.js не найден.", status_code=404)


# Маршруты для API
api_routes = [
    Route("/load_state", load_state_endpoint, methods=["POST"]),
    Route("/collect_income", collect_income_endpoint, methods=["POST"]),
    Route("/buy_sector", buy_sector_endpoint, methods=["POST"]),
]

# Маршруты для всего приложения (включая статику и основную страницу)
routes = [
    # --- ИСПРАВЛЕНИЕ: Обслуживаем app.js через отдельный обработчик (ВМЕСТО StaticFiles) ---
    Route("/app.js", app_js_endpoint, methods=["GET"]),
    
    # Главная страница и страница WebApp
    Route("/", homepage, methods=["GET"]),
    Route("/webapp", homepage, methods=["GET"]),
    
    # Эндпоинт для обмена токенами
    Route("/auth-token", auth_token_endpoint, methods=["POST"]),
    
    # API endpoints
    Mount("/api", routes=api_routes),
]

# Настройка CORS Middleware
middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
]

app = Starlette(
    routes=routes,
    middleware=middleware,
    exception_handlers={
        HTTPException: handle_http_exception
    },
    on_startup=[startup_event],
)
