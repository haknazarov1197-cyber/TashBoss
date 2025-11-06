import os
import sys
import logging
import json
from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Импорты Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, auth, firestore

# --- Константы и конфигурация игры ---

# Конфигурация секторов (должна совпадать с app.js)
SECTORS_CONFIG = {
    "sector1": {"name": "Сектор A (Киоски)", "passive_income": 0.5, "base_cost": 100.0},
    "sector2": {"name": "Сектор B (Кафе)", "passive_income": 2.0, "base_cost": 500.0},
    "sector3": {"name": "Сектор C (Офисы)", "passive_income": 10.0, "base_cost": 2500.0},
}

# Путь к коллекции в Firestore
# Используем appId для изоляции данных
APP_ID = "tashboss-clicker-app" # Идентификатор вашего приложения
COLLECTION_PATH = f"artifacts/{APP_ID}/users"

# --- Логирование ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("api")

# --- Глобальные клиенты Firebase (Инициализируются в startup_event) ---
db_client = None
auth_client = None

# --- Утилиты Firebase ---

def add_padding_if_needed(data: str) -> str:
    """
    Обеспечивает правильное заполнение данных Base64 для декодирования.
    Переменные окружения часто обрезают необходимые завершающие символы '='.
    """
    data = data.strip()
    padding_needed = len(data) % 4
    if padding_needed != 0:
        data += '=' * (4 - padding_needed)
    return data

def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя ключ из переменной окружения.
    """
    global db_client, auth_client
    
    FIREBASE_KEY_BASE64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    
    if not FIREBASE_KEY_BASE64:
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения 'FIREBASE_SERVICE_ACCOUNT_KEY' не найдена. Завершение работы.")
        sys.exit(1)

    try:
        # --- НОВОЕ ИСПРАВЛЕНИЕ: Очистка строки перед декодированием ---
        # Удаляем любые внешние кавычки или пробелы, которые могли быть добавлены
        clean_key = FIREBASE_KEY_BASE64.strip().strip("'").strip('"')
        
        # Применяем исправление: дополняем ключ символами '='
        padded_key = add_padding_if_needed(clean_key)
        
        # Декодируем исправленный ключ
        decoded_key_bytes = b64decode(padded_key)
        
        # Загружаем JSON
        # Если здесь происходит сбой с UnicodeDecodeError, это означает, что исходный Base64 был неверным.
        service_account_info = json.loads(decoded_key_bytes.decode('utf-8'))
        
        # Инициализируем Firebase
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        
        db_client = firestore.client()
        auth_client = auth
        
        logger.info("✅ Firebase успешно инициализирован и клиенты созданы.")
        
    except BinasciiError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка декодирования Base64. Вероятно, неверная строка: {e}.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Непредвиденная ошибка при инициализации Firebase: {e}")
        # Выводим дополнительную подсказку для отладки
        logger.critical("ПОДСКАЗКА: Ошибка 'UnicodeDecodeError' почти всегда указывает на поврежденный ключ FIREBASE_SERVICE_ACCOUNT_KEY в переменных окружения Render.")
        sys.exit(1)

# --- Настройка FastAPI ---

app = FastAPI(title="TashBoss Clicker API")

# 1. CORS Middleware: Разрешаем все источники для работы в Telegram WebApp
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Обработчик события запуска
@app.on_event("startup")
async def startup_event():
    # Инициализируем Firebase при запуске сервера
    init_firebase()

# 3. Обслуживание статических файлов (index.html, app.js)
app.mount("/app.js", StaticFiles(directory=".", html=True), name="app_js_static")
app.mount("/index.html", StaticFiles(directory=".", html=True), name="index_html_static")
# Обслуживание статических файлов, включая корневой путь
app.mount("/", StaticFiles(directory=".", html=True), name="static")

# --- Аутентификация: Зависимость FastAPI ---

async def get_auth_data(request: Request) -> str:
    """
    Извлекает и проверяет токен ID Firebase из заголовка Authorization.
    Возвращает UID пользователя, если токен действителен.
    """
    global auth_client
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Отсутствует или неверный заголовок аутентификации."
        )

    id_token = auth_header.split("Bearer ")[1]
    
    try:
        # Проверка токена с помощью Firebase Admin SDK
        # Эта функция также проверяет срок действия токена
        decoded_token = auth_client.verify_id_token(id_token)
        uid = decoded_token['uid']
        return uid
    except Exception as e:
        logger.error(f"Недействительный токен Firebase ID: {e}")
        raise HTTPException(
            status_code=401, detail="Недействительный токен аутентификации. Пожалуйста, перезапустите WebApp."
        )

# --- Утилиты Игры ---

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db_client.collection(f"{COLLECTION_PATH}/{user_id}/tashboss_clicker").document(user_id)

def calculate_passive_income(state: dict) -> float:
    """
    Рассчитывает пассивный доход, накопленный с момента last_collection_time.
    """
    last_time = state.get('last_collection_time')
    
    if not last_time:
        return 0.0

    # Firestore сохраняет datetime как Timestamp, преобразуем его
    if not isinstance(last_time, datetime):
         last_time = last_time.replace(tzinfo=None)

    now = datetime.utcnow()
    
    # Защита от слишком большого разрыва во времени (например, если серверное время сбросилось)
    if now <= last_time:
        return 0.0
        
    time_delta: timedelta = now - last_time
    # Конвертируем разницу во времени в секунды
    seconds_elapsed = time_delta.total_seconds()

    # Рассчитываем общий доход в секунду
    total_income_per_second = sum(
        SECTORS_CONFIG[sec]['passive_income'] * state['sectors'].get(sec, 0)
        for sec in SECTORS_CONFIG
    )

    return total_income_per_second * seconds_elapsed

def get_next_cost(sector_id: str, current_level: int) -> float:
    """Рассчитывает стоимость следующего уровня сектора."""
    config = SECTORS_CONFIG.get(sector_id)
    if not config:
        return float('inf')
    return config['base_cost'] * (current_level + 1)

# --- API Эндпоинты Игры ---

@app.post("/api/load_state")
async def load_state(user_id: str = Depends(get_auth_data)):
    """
    Загружает состояние игры пользователя или создает новое.
    """
    doc_ref = get_user_doc_ref(user_id)
    doc = doc_ref.get()

    if doc.exists:
        state = doc.to_dict()
        logger.info(f"Загружено состояние для пользователя: {user_id}")
    else:
        # Инициализация нового состояния
        initial_sectors = {s: 0 for s in SECTORS_CONFIG}
        state = {
            "user_id": user_id,
            "balance": 100.0,
            "sectors": initial_sectors,
            # Используем UTC для консистентности
            "last_collection_time": datetime.utcnow(), 
        }
        # Сохраняем начальное состояние
        doc_ref.set(state)
        logger.info(f"Создано новое состояние для пользователя: {user_id}")
        
    # Рассчитываем доступный доход (но не добавляем его к балансу)
    available_income = calculate_passive_income(state)
    state['available_income'] = available_income
    
    return state

@app.post("/api/collect_income")
async def collect_income(user_id: str = Depends(get_auth_data)):
    """
    Собирает накопленный пассивный доход и обновляет баланс.
    """
    doc_ref = get_user_doc_ref(user_id)
    
    # Используем транзакцию для безопасного обновления
    @firestore.transactional
    def transaction_update(transaction, doc_ref):
        snapshot = doc_ref.get(transaction=transaction)
        
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Состояние игры не найдено. Попробуйте перезапустить.")
            
        state = snapshot.to_dict()
        
        # 1. Рассчитываем накопленный доход
        collected_amount = calculate_passive_income(state)
        
        # 2. Обновляем баланс и время сбора
        if collected_amount > 0.01:
            state['balance'] += collected_amount
            state['last_collection_time'] = datetime.utcnow()
            
            # Обновляем документ в транзакции
            transaction.set(doc_ref, state)
            
            logger.info(f"Доход {collected_amount:.2f} собран пользователем {user_id}")

        # Добавляем информацию о собранной сумме в ответ
        state['collected_amount'] = collected_amount
        state['available_income'] = 0.0 # Сразу обнуляем доступный доход
        
        return state

    try:
        # Запускаем транзакцию
        updated_state = transaction_update(db_client.transaction(), doc_ref)
        return updated_state
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка транзакции сбора дохода для {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось собрать доход из-за ошибки базы данных.")


@app.post("/api/buy_sector")
async def buy_sector(request: Request, user_id: str = Depends(get_auth_data)):
    """
    Покупает следующий уровень сектора, уменьшает баланс.
    """
    try:
        data = await request.json()
        sector_id = data.get('sector_id')
    except:
        raise HTTPException(status_code=400, detail="Неверный формат запроса (ожидается JSON с sector_id).")
        
    if sector_id not in SECTORS_CONFIG:
        raise HTTPException(status_code=400, detail="Неизвестный идентификатор сектора.")
        
    doc_ref = get_user_doc_ref(user_id)

    @firestore.transactional
    def transaction_purchase(transaction, doc_ref):
        snapshot = doc_ref.get(transaction=transaction)
        
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
            
        state = snapshot.to_dict()
        current_level = state['sectors'].get(sector_id, 0)
        cost = get_next_cost(sector_id, current_level)
        
        # 1. Сначала собираем любой накопленный доход (важно!)
        collected_amount = calculate_passive_income(state)
        if collected_amount > 0.01:
            state['balance'] += collected_amount
            state['last_collection_time'] = datetime.utcnow()
        
        # 2. Проверяем баланс после сбора
        if state['balance'] < cost:
            # Возвращаем состояние с флагом неудачи, но без ошибки 400, так как доход был собран
            state['purchase_successful'] = False
            state['collected_amount'] = collected_amount
            state['available_income'] = 0.0 # Обнуляем
            return state

        # 3. Выполняем покупку
        state['balance'] -= cost
        state['sectors'][sector_id] = current_level + 1
        
        # Обновляем документ в транзакции
        transaction.set(doc_ref, state)
        
        logger.info(f"Пользователь {user_id} купил {sector_id}, теперь уровень {current_level + 1}")

        # Добавляем информацию о транзакции в ответ
        state['purchase_successful'] = True
        state['collected_amount'] = collected_amount
        state['available_income'] = 0.0 # Обнуляем
        
        return state

    try:
        updated_state = transaction_purchase(db_client.transaction(), doc_ref)
        return updated_state
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка транзакции покупки для {user_id}: {e}")
        # Возвращаем общую ошибку сервера
        return JSONResponse(
            status_code=500,
            content={"detail": "Не удалось совершить покупку из-за ошибки базы данных. Попробуйте еще раз."},
        )


# --- Роуты для обслуживания фронтенда ---

@app.get("/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_index_html():
    """Отдает файл index.html для Telegram WebApp."""
    try:
        # Читаем index.html и возвращаем его как HTMLResponse
        with open("index.html", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "Файл index.html не найден."})

# Если вы обслуживаете app.js через статический роут, этот роут не нужен, 
# так как /app.js обрабатывается app.mount("/", ...), но оставим его как запасной вариант.
@app.get("/app.js", response_class=HTMLResponse)
async def serve_app_js():
    """Отдает файл app.js."""
    try:
        with open("app.js", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content, media_type="application/javascript")
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "Файл app.js не найден."})

# --- Роут для получения Custom Token (для index.html) ---

@app.post("/auth-token")
async def get_custom_token(request: Request):
    """
    Принимает Telegram User ID и генерирует Custom Token Firebase.
    Это позволяет frontend-скрипту войти в Firebase.
    """
    global auth_client
    
    try:
        data = await request.json()
        telegram_user_id = data.get('telegram_user_id')
        
        if not telegram_user_id:
            raise HTTPException(status_code=400, detail="Требуется 'telegram_user_id'.")
            
        # 1. Создание или получение UID пользователя Firebase, привязанного к Telegram ID
        try:
            # Пробуем найти существующего пользователя по его Telegram ID
            user = auth_client.get_user_by_provider_uid("telegram.com", telegram_user_id)
            uid = user.uid
        except auth.UserNotFoundError:
            # Если не найден, создаем нового пользователя, используя Telegram ID как UID
            # Это критично для дальнейшего маппинга
            # Примечание: Для более сложной интеграции лучше использовать set_custom_user_claims 
            # или создать нового пользователя и связать его с Telegram ID.
            # Для простоты мы создаем пользователя, где UID = Telegram ID.
            user = auth_client.create_user(uid=telegram_user_id)
            uid = user.uid
            logger.info(f"Создан новый пользователь Firebase с UID: {uid}")

        # 2. Генерация Custom Token
        custom_token = auth_client.create_custom_token(uid)
        
        return {"token": custom_token.decode("utf-8")}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при генерации Custom Token: {e}")
        raise HTTPException(
            status_code=500, detail="Не удалось сгенерировать токен Firebase."
        )

# Создаем переменную 'app' для Gunicorn
# gunicorn api:app
