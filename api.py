import os
import json
import logging
import time

# --- Firebase Imports ---
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore 

# --- FastAPI/Uvicorn Imports ---
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel 

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =======================================================
# 1. ОСНОВНОЕ ПРИЛОЖЕНИЕ FASTAPI (КРИТИЧЕСКИЙ БЛОК)
# =======================================================
app = FastAPI()

# Глобальные переменные для Firebase
db = None
APP_ID = "tashboss_game" # Ваш уникальный идентификатор приложения

# --- Конфигурация Игры ---
INITIAL_INDUSTRIES_CONFIG = {
    # Сектор 1: Базовый (стартовый, бесплатный)
    "1": {"name": "Базовый Сектор", "base_cost": 0, "base_income": 1}, 
    # Сектор 2: Первая покупка
    "2": {"name": "Ферма", "base_cost": 5000, "base_income": 10},
    # Сектор 3: Вторая покупка
    "3": {"name": "Магазин", "base_cost": 25000, "base_income": 50},
}

# --- Pydantic Models для запросов ---
class CollectIncomeRequest(BaseModel):
    user_id: int # Telegram ID пользователя
    sector_id: str

class BuySectorRequest(BaseModel):
    user_id: int
    sector_id: str

# =======================================================
# 2. ИНИЦИАЛИЗАЦИЯ FIREBASE
# =======================================================
def initialize_firebase():
    """Инициализирует Firebase и создает клиент Firestore."""
    global db
    
    creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    
    if not creds_json:
        logger.warning("WARNING: Переменная среды 'FIREBASE_CREDENTIALS_JSON' не установлена.")
        return 

    try:
        creds_dict = json.loads(creds_json)
        cred = credentials.Certificate(creds_dict)
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info("INFO: Firebase успешно инициализирован и Firestore client готов к работе.")
        
    except json.JSONDecodeError:
        logger.error("CRITICAL ERROR: Ошибка декодирования JSON-ключа Firebase. Проверьте формат.")
    except Exception as e:
        logger.error(f"CRITICAL ERROR: Не удалось инициализировать Firebase: {e}")

# Запускаем инициализацию при старте приложения
initialize_firebase()


# --- Firestore Helpers (Вспомогательные функции) ---

def get_player_ref(user_id: int):
    """Возвращает ссылку на документ игрока в публичной коллекции."""
    if not db:
        raise Exception("Database not initialized. Check FIREBASE_CREDENTIALS_JSON.")
    
    # Путь: /artifacts/{APP_ID}/public/data/players/{user_id}
    return db.collection(f"artifacts/{APP_ID}/public/data/players").document(str(user_id))

def get_initial_player_data(user_id: int) -> dict:
    """Возвращает начальное состояние игрока."""
    return {
        "user_id": user_id,
        "balance": 1000,
        "last_login": int(time.time()),
        "sectors": {
            "1": {
                "income_per_second": INITIAL_INDUSTRIES_CONFIG["1"]["base_income"],
                "last_collect_time": int(time.time()),
                "level": 1
            }
        },
        "industries_config": INITIAL_INDUSTRIES_CONFIG
    }

# =======================================================
# 3. ОСНОВНЫЕ API ENDPOINTS
# =======================================================

@app.head("/")
async def head_root():
    """Обрабатывает HEAD-запросы для проверки активности сервиса (используется Render)."""
    return {"status": "ok"}

@app.get("/")
async def root():
    """Основная конечная точка для проверки работоспособности."""
    return {"message": "Tashboss Game API is running."}

@app.get("/api/load_state")
async def load_state(user_id: int):
    """
    Загружает состояние игрока и рассчитывает накопленную прибыль.
    """
    try:
        player_ref = get_player_ref(user_id)
        player_doc = player_ref.get()

        if not player_doc.exists:
            # Создаем нового игрока
            initial_data = get_initial_player_data(user_id)
            player_ref.set(initial_data)
            data = initial_data
            logger.info(f"Создан новый игрок: {user_id}")
        else:
            data = player_doc.to_dict()

        # --- Логика расчета накопленного дохода ---
        total_accumulated_income = 0
        current_time = int(time.time())
        
        for sector_id, sector in data.get("sectors", {}).items():
            last_collect = sector.get("last_collect_time", current_time)
            income_per_second = sector.get("income_per_second", 0)
            
            # Убеждаемся, что accumulated всегда float или int (без ошибок в расчетах)
            time_elapsed = max(0, current_time - last_collect)
            accumulated = int(time_elapsed * income_per_second)
            
            sector["income_to_collect"] = accumulated
            total_accumulated_income += accumulated

        data["total_accumulated_income"] = total_accumulated_income

        return data

    except Exception as e:
        logger.error(f"Ошибка при загрузке состояния игрока {user_id}: {e}")
        # Возвращаем ошибку в JSON, чтобы фронтенд мог ее обработать
        return {"error": str(e)}

@app.post("/api/collect_income")
async def collect_income(request: CollectIncomeRequest):
    """
    Обрабатывает запрос на сбор дохода для конкретного сектора.
    """
    user_id = request.user_id
    sector_id = request.sector_id
    
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    player_ref = get_player_ref(user_id)
    current_time = int(time.time())
    
    @firestore.transactional
    def transaction_update(transaction, player_ref):
        snapshot = player_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Игрок не найден.")
        
        data = snapshot.to_dict()
        sector = data.get("sectors", {}).get(sector_id)
        
        if not sector:
            raise HTTPException(status_code=400, detail="Сектор не найден.")

        last_collect = sector.get("last_collect_time", current_time)
        income_per_second = sector.get("income_per_second", 0)
        
        time_elapsed = current_time - last_collect
        collected_amount = int(time_elapsed * income_per_second)
        
        if collected_amount < 1:
            return {"success": False, "message": "Ещё нет дохода для сбора."}

        # Обновление данных
        new_balance = data.get("balance", 0) + collected_amount
        
        # Обновляем время последнего сбора и баланс
        data["balance"] = new_balance
        data["sectors"][sector_id]["last_collect_time"] = current_time
        
        transaction.set(player_ref, data)
        
        return {
            "success": True,
            "new_balance": new_balance,
            "collected": collected_amount
        }

    try:
        result = transaction_update(db.transaction(), player_ref)
        if result.get("success") is False:
             raise HTTPException(status_code=400, detail=result.get("message"))
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка транзакции сбора дохода для {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера при сборе дохода.")

@app.post("/api/buy_sector")
async def buy_sector(request: BuySectorRequest):
    """
    Покупает новый сектор, если у игрока достаточно средств.
    """
    user_id = request.user_id
    sector_id_to_buy = request.sector_id
    
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    config = INITIAL_INDUSTRIES_CONFIG.get(sector_id_to_buy)
    
    if not config:
        raise HTTPException(status_code=400, detail="Неверный ID сектора.")

    player_ref = get_player_ref(user_id)
    current_time = int(time.time())
    cost = config["base_cost"]

    @firestore.transactional
    def transaction_buy(transaction, player_ref):
        snapshot = player_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Игрок не найден.")
        
        data = snapshot.to_dict()
        current_balance = data.get("balance", 0)

        # 1. Проверка: не куплен ли уже сектор?
        if sector_id_to_buy in data.get("sectors", {}):
            return {"success": False, "message": "Сектор уже куплен."}

        # 2. Проверка: достаточно ли средств?
        if current_balance < cost:
            return {"success": False, "message": "Недостаточно средств для покупки."}

        # 3. Проводим покупку
        new_balance = current_balance - cost
        
        data["balance"] = new_balance
        
        # Добавляем новый сектор с его базовым доходом
        data["sectors"][sector_id_to_buy] = {
            "income_per_second": config["base_income"],
            "last_collect_time": current_time,
            "level": 1
        }
        
        transaction.set(player_ref, data)
        
        return {
            "success": True,
            "new_balance": new_balance,
            "cost": cost,
            "sector_name": config["name"]
        }

    try:
        result = transaction_buy(db.transaction(), player_ref)
        if result.get("success") is False:
             raise HTTPException(status_code=400, detail=result.get("message"))
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка транзакции покупки сектора {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера при покупке сектора.")
