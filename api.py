import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from firebase_admin import credentials, initialize_app, firestore, auth
from google.cloud.firestore import Client, Transaction
from pydantic import BaseModel

# Импортируем измененную функцию из bot.py
from bot import get_telegram_application
from telegram import Update

# --- 1. Инициализация Firebase и Приложения ---

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальные переменные Firebase
db: Optional[Client] = None
APP_ID = "tashboss" # Условный ID для пути Firestore

# Получение ключа сервисного аккаунта из переменной окружения
FIREBASE_KEY_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")

if FIREBASE_KEY_JSON:
    try:
        # Парсинг JSON ключа
        service_account_info = json.loads(FIREBASE_KEY_JSON)
        cred = credentials.Certificate(service_account_info)
        # Инициализация Firebase
        firebase_app = initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase успешно инициализирован.")
    except Exception as e:
        logger.critical(f"Критическая ошибка инициализации Firebase: {e}")
        # В реальном приложении здесь можно поднять исключение, чтобы остановить запуск
        # Но для простоты оставим как есть, чтобы не прерывать FastAPI, но логика API будет падать.
else:
    logger.critical("Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY не найдена.")

# Инициализация FastAPI
app = FastAPI(title="TashBoss Clicker API")

# Инициализация Telegram Application (для вебхуков)
tg_app = get_telegram_application()

# --- 2. Настройка CORS Middleware (Критически важно для WebApp) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем все источники, что необходимо для Telegram WebApp
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 3. Аутентификация и Зависимости ---

async def get_auth_data(request: Request) -> str:
    """Извлекает и верифицирует токен Firebase, возвращает UID пользователя."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Отсутствует заголовок аутентификации.")

    token = auth_header.split(" ")[1]
    
    try:
        # Проверяем токен с помощью Firebase Admin SDK
        # В идеале Mini App должен передавать JWT, сгенерированный вашим бэкендом
        # На данный момент мы полагаемся на то, что фронтенд передает query_id (см. app.js)
        # Для этой архитектуры мы используем verify_id_token для проверки токена.
        # В реальной Mini App интеграции требуется более сложная валидация.
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        return uid
    except Exception as e:
        logger.error(f"Ошибка верификации токена: {e}")
        raise HTTPException(status_code=401, detail="Недействительный токен или истек срок действия.")

# --- 4. Модели Pydantic для API ---

class GameState(BaseModel):
    balance: float
    sectors: Dict[str, int]
    last_collection_time: str # Хранится как ISO string
    
class BuySectorRequest(BaseModel):
    sector_id: str
    
# --- 5. Логика Игры и Firestore ---

DEFAULT_STATE = {
    'balance': 100.0,
    'sectors': {"sector1": 0, "sector2": 0, "sector3": 0},
    'last_collection_time': datetime.now(timezone.utc).isoformat()
}

SECTOR_DATA = {
    "sector1": {"income": 0.5, "cost": 100.0},
    "sector2": {"income": 2.0, "cost": 500.0},
    "sector3": {"income": 10.0, "cost": 2500.0},
}

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    if not db:
        raise HTTPException(status_code=500, detail="База данных не инициализирована.")
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db.collection("artifacts").document(APP_ID).collection("users").document(user_id).collection("tashboss_clicker").document(user_id)


def calculate_income(state: dict) -> float:
    """Рассчитывает общий доход в секунду на основе уровней секторов."""
    total_income = 0.0
    for key, count in state['sectors'].items():
        if key in SECTOR_DATA:
            total_income += SECTOR_DATA[key]['income'] * count
    return total_income

# --- 6. Эндпоинты API с Транзакциями ---

@app.post("/api/load_state", response_model=GameState)
@firestore.transactional
async def load_state(transaction: Transaction, user_id: str = Depends(get_auth_data)):
    """Загружает или инициализирует состояние игры пользователя."""
    doc_ref = get_user_doc_ref(user_id)
    doc = doc_ref.get(transaction=transaction)

    if doc.exists:
        state = doc.to_dict()
        # В Firestore Timestamp хранится как datetime, преобразуем в ISO string для фронтенда
        if isinstance(state.get('last_collection_time'), datetime):
             state['last_collection_time'] = state['last_collection_time'].isoformat()
        return GameState(**state)
    else:
        # Инициализация нового пользователя
        new_state = DEFAULT_STATE.copy()
        new_state['last_collection_time'] = datetime.now(timezone.utc).isoformat()
        doc_ref.set(new_state, transaction=transaction)
        return GameState(**new_state)

@app.post("/api/collect_income", response_model=GameState)
@firestore.transactional
async def collect_income(transaction: Transaction, user_id: str = Depends(get_auth_data)):
    """Рассчитывает накопленный доход и добавляет его к балансу."""
    doc_ref = get_user_doc_ref(user_id)
    doc = doc_ref.get(transaction=transaction)

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Состояние игры не найдено.")
    
    state = doc.to_dict()
    
    # Преобразование времени сбора
    last_collection_dt = datetime.fromisoformat(state['last_collection_time'])
    now_dt = datetime.now(timezone.utc)
    
    # Расчет времени, прошедшего в секундах
    time_elapsed_seconds = (now_dt - last_collection_dt).total_seconds()
    
    total_income_per_second = calculate_income(state)
    collected_amount = time_elapsed_seconds * total_income_per_second

    # Обновление состояния
    state['balance'] = round(state['balance'] + collected_amount, 2)
    state['last_collection_time'] = now_dt.isoformat()
    
    doc_ref.set(state, transaction=transaction)
    
    return GameState(**state)


@app.post("/api/buy_sector", response_model=GameState)
@firestore.transactional
async def buy_sector(transaction: Transaction, sector_request: BuySectorRequest, user_id: str = Depends(get_auth_data)):
    """Покупает один уровень сектора."""
    sector_id = sector_request.sector_id
    if sector_id not in SECTOR_DATA:
        raise HTTPException(status_code=400, detail="Неверный ID сектора.")

    sector_info = SECTOR_DATA[sector_id]
    cost = sector_info['cost']
    
    doc_ref = get_user_doc_ref(user_id)
    doc = doc_ref.get(transaction=transaction)

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Состояние игры не найдено.")

    state = doc.to_dict()

    # Сначала собираем любой накопленный доход, чтобы учесть его при покупке
    last_collection_dt = datetime.fromisoformat(state['last_collection_time'])
    now_dt = datetime.now(timezone.utc)
    time_elapsed_seconds = (now_dt - last_collection_dt).total_seconds()
    total_income_per_second = calculate_income(state)
    collected_amount = time_elapsed_seconds * total_income_per_second
    
    state['balance'] = round(state['balance'] + collected_amount, 2)
    state['last_collection_time'] = now_dt.isoformat()

    # Проверка баланса
    if state['balance'] < cost:
        raise HTTPException(status_code=400, detail="Недостаточно BossCoin (BSS).")
    
    # Обновление состояния
    state['balance'] = round(state['balance'] - cost, 2)
    state['sectors'][sector_id] = state['sectors'].get(sector_id, 0) + 1
    
    doc_ref.set(state, transaction=transaction)

    return GameState(**state)


# --- 7. Маршруты для Telegram и Статических Файлов ---

@app.post(f"/webhook")
async def telegram_webhook(request: Request):
    """Обрабатывает входящий вебхук от Telegram."""
    try:
        # Получаем тело запроса
        body = await request.json()
        
        # Создаем объект Update
        update = Update.de_json(body, tg_app.bot)

        # Обрабатываем обновление
        await tg_app.process_update(update)
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        # Возвращаем 200, чтобы Telegram не пытался повторить отправку
        return {"status": "error", "message": str(e)}

@app.get("/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def get_index():
    """Возвращает основной файл index.html для Mini App."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="index.html не найден.")

# Обслуживание статических файлов (CSS, JS)
app.mount("/", StaticFiles(directory=".", html=False), name="static")
