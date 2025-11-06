import os
import sys
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# NOTE: Убедитесь, что 'firebase-admin' установлен в Вашем виртуальном окружении
try:
    import firebase_admin
    from firebase_admin import credentials, initialize_app, firestore, auth
except ImportError:
    print("Firebase Admin SDK не установлен. Установите его с помощью 'pip install firebase-admin'")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ CANVAS / FIREBASE ---
# Используем глобальную переменную Canvas для ID приложения
TASHBOSS_APP_ID = os.environ.get('__app_id', 'default-app-id') 

db: Optional[firestore.client] = None
app_firebase: Optional[firebase_admin.App] = None 

# --- КОНФИГУРАЦИЯ ИГРЫ ---
SECTOR_CONFIG = {
    "sector1": {"name": "Сектор 'А'", "passive_income": 0.5, "base_cost": 100.0},
    "sector2": {"name": "Сектор 'B'", "passive_income": 2.0, "base_cost": 500.0},
    "sector3": {"name": "Сектор 'C'", "passive_income": 10.0, "base_cost": 2500.0},
}
MAX_COLLECTION_DAYS = 7 # Максимальное накопление за 7 дней

# --- ИСПРАВЛЕННАЯ ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ FIREBASE ---
def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя учетные данные Service Account.
    Включает фикс для переносов строк в приватном ключе.
    """
    global db, app_firebase
    
    # Используем переменную, которая была причиной сбоя, и теперь фиксирована
    service_account_json_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')

    if not service_account_json_str:
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения 'FIREBASE_SERVICE_ACCOUNT_JSON' отсутствует.")
        sys.exit(1)
        
    try:
        service_account_info: Dict[str, Any] = json.loads(service_account_json_str)

        # ФИКС: Заменяем буквальные подстроки '\n' на фактические символы новой строки.
        private_key = service_account_info.get('private_key')
        if private_key and isinstance(private_key, str):
            service_account_info['private_key'] = private_key.replace('\\n', '\n')
            logger.info("Private key cleaning successful.")
        else:
            logger.warning("Поле приватного ключа отсутствует или не является строкой в JSON.")

        cred = credentials.Certificate(service_account_info)
        
        # Предотвращаем повторную инициализацию в процессах Gunicorn
        if not firebase_admin._apps:
            app_firebase = initialize_app(cred)
            logger.info("✅ Firebase Admin SDK успешно инициализирован.")
        else:
            # Получаем уже инициализированное приложение
            app_firebase = list(firebase_admin.get_apps())[0] 

        db = firestore.client(app=app_firebase)
        logger.info("✅ Клиент Firestore готов.")

    except ValueError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации Firebase: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось декодировать JSON учетной записи сервиса: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Общая ошибка инициализации Firebase: {e}")
        sys.exit(1)

# --- ИНИЦИАЛИЗАЦИЯ FASTAPI ---
app = FastAPI(title="TashBoss Clicker API")

# Настройка CORS: КРИТИЧНО для работы WebApp
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем все источники (Telegram WebApp)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ДЕПЕНДЕНСИ (АУТЕНТИФИКАЦИЯ) ---
async def get_current_user(request: Request):
    """Извлекает и проверяет токен Firebase ID из заголовка Authorization."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис Firebase недоступен."
        )

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не предоставлен Bearer токен."
        )

    id_token = auth_header.split(" ")[1]
    
    try:
        # Проверяем токен с помощью Firebase Admin SDK
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        return uid
    except Exception as e:
        logger.error(f"Ошибка проверки токена Firebase: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или просроченный токен Firebase."
        )

# --- УТИЛИТЫ FIREBASE ---
def get_user_doc_ref(uid: str) -> firestore.DocumentReference:
    """Возвращает ссылку на документ пользователя в Firestore."""
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db.collection('artifacts').document(TASHBOSS_APP_ID).collection('users').document(uid).collection('tashboss_clicker').document(uid)

def get_initial_state():
    """Создает начальное состояние игры."""
    return {
        'balance': 100.0,
        'sectors': {id: 0 for id in SECTOR_CONFIG.keys()},
        # Firestore Timestamp (как в JS-клиенте)
        'last_collection_time': {'_seconds': int(datetime.now().timestamp())},
        'available_income': 0.0, # Доступный доход, накопленный до последнего сбора
    }

# --- ИГРОВАЯ ЛОГИКА ---

def calculate_income(game_state: Dict[str, Any]) -> float:
    """
    Рассчитывает доход, накопленный с момента last_collection_time.
    Возвращает накопленную сумму и обновляет available_income в состоянии.
    """
    
    # 1. Рассчитываем общий доход в секунду
    total_income_per_second = 0.0
    for sector_id, level in game_state.get('sectors', {}).items():
        config = SECTOR_CONFIG.get(sector_id)
        if config and level > 0:
            total_income_per_second += config['passive_income'] * level
            
    if total_income_per_second == 0:
        return 0.0
        
    # 2. Получаем время последней коллекции
    last_time_data = game_state.get('last_collection_time')
    if not last_time_data or '_seconds' not in last_time_data:
        # Если время не определено, используем текущее время (никакого накопления)
        return 0.0
        
    last_collection_ts = last_time_data.get('_seconds', 0)
    last_collection_time = datetime.fromtimestamp(last_collection_ts)
    
    now = datetime.now()
    
    # 3. Максимальное ограничение времени накопления
    max_duration = timedelta(days=MAX_COLLECTION_DAYS)
    
    # Время, прошедшее с момента последнего сбора, ограниченное максимальной продолжительностью
    time_delta = now - last_collection_time
    
    if time_delta > max_duration:
        time_delta = max_duration
        
    time_in_seconds = time_delta.total_seconds()
    
    # 4. Рассчитываем накопленный доход
    newly_accrued_income = total_income_per_second * time_in_seconds
    
    return newly_accrued_income

# --- API ЭНДПОИНТЫ ---

@app.on_event("startup")
async def startup_event():
    """Обработчик события запуска приложения."""
    logger.info("Запуск приложения Starlette...")
    init_firebase()

# 1. Сервинг статических файлов (index.html и app.js)
app.mount("/app.js", StaticFiles(directory="."), name="appjs")
app.mount("/index.html", StaticFiles(directory="."), name="indexhtml")

@app.get("/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_index():
    """Обслуживает главный файл index.html."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Файл index.html не найден.")

# 2. Загрузка состояния игры
@app.post("/api/load_state")
async def load_state(uid: str = Depends(get_current_user)):
    """Загружает или инициализирует состояние игры пользователя."""
    doc_ref = get_user_doc_ref(uid)
    
    @firestore.transactional
    def transactional_load(transaction):
        snapshot = doc_ref.get(transaction=transaction)
        
        if snapshot.exists:
            game_state = snapshot.to_dict()
            # Добавляем доход, накопленный с момента последнего сбора
            newly_accrued_income = calculate_income(game_state)
            
            # Для клиента, показываем доступный доход (доступный + новый)
            game_state['available_income'] = game_state.get('available_income', 0.0) + newly_accrued_income
            return game_state
        else:
            initial_state = get_initial_state()
            transaction.set(doc_ref, initial_state)
            # В начальном состоянии доступный доход 0.0
            return initial_state

    try:
        return transactional_load(db.transaction())
    except Exception as e:
        logger.error(f"Ошибка при загрузке/инициализации состояния для {uid}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить состояние игры.")

# 3. Сбор дохода
@app.post("/api/collect_income")
async def collect_income(uid: str = Depends(get_current_user)):
    """Рассчитывает и добавляет накопленный доход к балансу."""
    doc_ref = get_user_doc_ref(uid)
    
    @firestore.transactional
    def transactional_collect(transaction):
        snapshot = doc_ref.get(transaction=transaction)
        if not snapshot.exists:
            # Если состояние не существует, инициализируем его, а затем собираем (0)
            initial_state = get_initial_state()
            transaction.set(doc_ref, initial_state)
            return initial_state

        game_state = snapshot.to_dict()
        
        # 1. Рассчитываем доход, накопленный с момента last_collection_time
        newly_accrued_income = calculate_income(game_state)
        
        # 2. Общий собираемый доход: доступный (от предыдущих сборов) + новый
        collected_amount = game_state.get('available_income', 0.0) + newly_accrued_income
        
        if collected_amount < 0.01:
            # Если дохода нет, просто обновляем время сбора и выходим
            game_state['last_collection_time'] = {'_seconds': int(datetime.now().timestamp())}
            game_state['available_income'] = 0.0
            game_state['collected_amount'] = 0.0 # Для ответа клиенту
            transaction.set(doc_ref, game_state)
            return game_state

        # 3. Обновляем баланс
        game_state['balance'] = game_state.get('balance', 0.0) + collected_amount
        
        # 4. Обновляем время сбора и сбрасываем доступный доход
        game_state['last_collection_time'] = {'_seconds': int(datetime.now().timestamp())}
        game_state['available_income'] = 0.0
        game_state['collected_amount'] = collected_amount # Для ответа клиенту

        transaction.set(doc_ref, game_state)
        return game_state

    try:
        return transactional_collect(db.transaction())
    except Exception as e:
        logger.error(f"Ошибка при сборе дохода для {uid}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось собрать доход.")

# 4. Покупка сектора
@app.post("/api/buy_sector")
async def buy_sector(request: Request, uid: str = Depends(get_current_user)):
    """Покупает сектор, если достаточно средств."""
    try:
        body = await request.json()
        sector_id = body.get('sector_id')
    except:
        raise HTTPException(status_code=400, detail="Неверный формат запроса.")
        
    if sector_id not in SECTOR_CONFIG:
        raise HTTPException(status_code=400, detail="Неизвестный ID сектора.")
        
    doc_ref = get_user_doc_ref(uid)
    
    @firestore.transactional
    def transactional_buy(transaction):
        snapshot = doc_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
            
        game_state = snapshot.to_dict()
        
        # 1. Сначала рассчитываем и собираем весь доход
        newly_accrued_income = calculate_income(game_state)
        collected_amount = game_state.get('available_income', 0.0) + newly_accrued_income
        
        if collected_amount > 0:
            game_state['balance'] += collected_amount
            game_state['last_collection_time'] = {'_seconds': int(datetime.now().timestamp())}
            game_state['available_income'] = 0.0
        
        # Сохраняем собранное количество для ответа клиенту
        game_state['collected_amount'] = collected_amount
        
        # 2. Логика покупки
        config = SECTOR_CONFIG[sector_id]
        current_level = game_state['sectors'].get(sector_id, 0)
        cost = config['base_cost'] * (current_level + 1)
        
        if game_state['balance'] >= cost:
            # Покупка успешна
            game_state['balance'] -= cost
            game_state['sectors'][sector_id] = current_level + 1
            game_state['purchase_successful'] = True
        else:
            # Покупка неуспешна
            game_state['purchase_successful'] = False
            
        transaction.set(doc_ref, game_state)
        return game_state

    try:
        return transactional_buy(db.transaction())
    except HTTPException:
        # Передаем ошибки HTTPException (например, 404)
        raise
    except Exception as e:
        logger.error(f"Ошибка при покупке сектора для {uid}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось совершить покупку.")

# Запуск: Gunicorn будет искать 'app'
