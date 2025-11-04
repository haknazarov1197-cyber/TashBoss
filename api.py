import os
import sys
import json
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleWARE
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

# Импорты для Firebase/Firestore
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# --- КОНФИГУРАЦИЯ ---

# Определяем базовую директорию для надежного поиска index.html
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Инициализация логгирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("api")

# Конфигурация секторов (для новой логики)
INDUSTRY_CONFIG = {
    "sector_1": {"name": "Базовый Сектор", "base_income": 1.0, "cost": 0},
    "sector_2": {"name": "Производство чипсов", "base_income": 5.0, "cost": 1000},
    "sector_3": {"name": "Сеть кофеен", "base_income": 25.0, "cost": 5000},
    "sector_4": {"name": "Медиахолдинг", "base_income": 100.0, "cost": 25000},
}

# --- ИНИЦИАЛИЗАЦИЯ FASTAPI ---

app = FastAPI(title="Tashboss Game API")

# Настройка CORS для работы WebApp
origins = ["*"] # Разрешаем все источники для простоты развертывания WebApp
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ИНИЦИАЛИЗАЦИЯ FIREBASE ---

# Получение учетных данных из переменной окружения
FIREBASE_SERVICE_ACCOUNT_KEY = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")

if FIREBASE_SERVICE_ACCOUNT_KEY:
    try:
        # Парсинг JSON-ключа
        cred_json = json.loads(FIREBASE_SERVICE_ACCOUNT_KEY)
        cred = credentials.Certificate(cred_json)
        
        # Инициализация Firebase
        firebase_app = firebase_admin.initialize_app(cred)
        db = firestore.client()
        
        logger.info("INFO: Firebase успешно инициализирован и Firestore client готов к работе.")
    except Exception as e:
        logger.error(f"ERROR: Ошибка инициализации Firebase: {e}")
        # Выходим, если не удалось инициализировать Firebase
        sys.exit(1)
else:
    logger.error("ERROR: Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY не найдена.")
    sys.exit(1)

# --- PYDANTIC МОДЕЛИ ---

class UserState(BaseModel):
    user_id: int = Field(..., description="Telegram User ID")

class CollectIncomeRequest(BaseModel):
    user_id: int = Field(..., description="Telegram User ID")
    
class BuySectorRequest(BaseModel):
    user_id: int = Field(..., description="Telegram User ID")
    sector_id: str = Field(..., description="ID сектора для покупки (e.g., 'sector_2')")

# --- СТАТИЧЕСКИЕ ФАЙЛЫ (КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ) ---

# Монтирование статических файлов из BASE_DIR, чтобы FastAPI отдавал index.html
# при запросе корневого URL /
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")

# Удаляем корневой GET-роут, так как его заменяет StaticFiles.
# Оставляем только HEAD для проверок здоровья (health checks).
@app.head("/")
async def head_root():
    return status.HTTP_200_OK

# --- УТИЛИТЫ FIREBASE ---

def get_user_doc_ref(user_id: int):
    """Возвращает ссылку на документ пользователя в коллекции 'users'."""
    return db.collection("users").document(str(user_id))

def create_initial_state():
    """Создает начальное состояние игры для нового пользователя."""
    now = datetime.utcnow()
    return {
        "balance": 1000.0,
        "last_collection_time": now.isoformat(),
        "sectors": {
            "sector_1": {"count": 1, "income_rate": INDUSTRY_CONFIG["sector_1"]["base_income"]},
        },
        "version": 1,
    }

def calculate_income(user_state: dict) -> tuple[float, datetime]:
    """Рассчитывает доход с момента последней коллекции."""
    
    last_collection = datetime.fromisoformat(user_state["last_collection_time"])
    now = datetime.utcnow()
    
    time_elapsed = now - last_collection
    seconds_elapsed = time_elapsed.total_seconds()
    
    # Расчет общей ставки дохода в секунду
    total_income_rate = sum(
        sector["count"] * sector["income_rate"]
        for sector in user_state["sectors"].values()
    )
    
    # Расчет накопленного дохода
    income_gained = total_income_rate * seconds_elapsed
    
    return income_gained, now

# --- КОНЕЧНЫЕ ТОЧКИ API ---

@app.post("/api/load_state")
async def load_state(request: UserState):
    """
    Загружает текущее состояние пользователя. Создает новое состояние, если пользователь не найден.
    """
    doc_ref = get_user_doc_ref(request.user_id)
    doc = await doc_ref.get()

    if doc.exists:
        user_state = doc.to_dict()
        
        # Дополнительный расчет для фронтенда: сколько дохода накопилось
        income_gained, _ = calculate_income(user_state)
        user_state["current_pending_income"] = round(income_gained, 2)
        
        logger.info(f"Loaded state for user {request.user_id}")
        return {"status": "ok", "state": user_state, "config": INDUSTRY_CONFIG}
    else:
        # Создаем начальное состояние
        initial_state = create_initial_state()
        await doc_ref.set(initial_state)
        
        initial_state["current_pending_income"] = 0.0
        
        logger.info(f"Created initial state for user {request.user_id}")
        return {"status": "new_user", "state": initial_state, "config": INDUSTRY_CONFIG}

@app.post("/api/collect_income")
async def collect_income(request: CollectIncomeRequest):
    """
    Рассчитывает и добавляет накопленный доход к балансу пользователя.
    """
    doc_ref = get_user_doc_ref(request.user_id)
    doc = await doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")

    user_state = doc.to_dict()
    income_gained, now = calculate_income(user_state)

    if income_gained > 0:
        new_balance = user_state["balance"] + income_gained
        
        # Обновляем состояние в Firestore
        update_data = {
            "balance": round(new_balance, 2),
            "last_collection_time": now.isoformat(),
        }
        await doc_ref.update(update_data)

        logger.info(f"Collected {income_gained:.2f} income for user {request.user_id}")
        return {
            "status": "collected",
            "income": round(income_gained, 2),
            "new_balance": round(new_balance, 2),
            "last_collection_time": now.isoformat(),
        }
    else:
        logger.info(f"No income to collect for user {request.user_id}")
        return {"status": "no_income", "income": 0.0, "new_balance": round(user_state["balance"], 2)}


@app.post("/api/buy_sector")
async def buy_sector(request: BuySectorRequest):
    """
    Позволяет пользователю купить новый сектор, вычитает стоимость из баланса.
    """
    sector_id = request.sector_id
    
    if sector_id not in INDUSTRY_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid sector ID")
        
    sector_config = INDUSTRY_CONFIG[sector_id]
    cost = sector_config["cost"]

    doc_ref = get_user_doc_ref(request.user_id)

    # Используем транзакцию для атомарной операции (чтение и запись)
    @firestore.transactional
    async def transaction_buy(transaction, doc_ref, sector_id, cost, sector_config):
        snapshot = await doc_ref.get(transaction=transaction)
        
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="User not found during transaction")
        
        user_state = snapshot.to_dict()
        
        # 1. Сначала рассчитываем накопленный доход (важно для точности)
        income_gained, now = calculate_income(user_state)
        user_state["balance"] += income_gained

        if user_state["balance"] < cost:
            # Возвращаем текущий баланс и доход, чтобы фронтенд обновил UI
            return {
                "status": "insufficient_funds",
                "current_balance": round(user_state["balance"], 2),
                "required_cost": cost,
                "income_collected": round(income_gained, 2),
            }

        # 2. Выполняем покупку
        new_balance = user_state["balance"] - cost
        
        # Обновляем количество секторов
        sectors = user_state.get("sectors", {})
        sectors[sector_id] = {
            "count": sectors.get(sector_id, {}).get("count", 0) + 1,
            "income_rate": sector_config["base_income"]
        }
        
        # 3. Обновляем документ
        update_data = {
            "balance": round(new_balance, 2),
            "sectors": sectors,
            "last_collection_time": now.isoformat(), # Сбрасываем время после сбора/покупки
        }
        transaction.update(doc_ref, update_data)
        
        logger.info(f"User {request.user_id} bought sector {sector_id}. New balance: {new_balance:.2f}")
        return {
            "status": "success",
            "new_balance": round(new_balance, 2),
            "new_sectors": sectors,
            "income_collected": round(income_gained, 2),
        }

    try:
        # Запуск транзакции
        return await transaction_buy(db.transaction(), doc_ref, sector_id, cost, sector_config)
    except Exception as e:
        logger.error(f"Transaction error for user {request.user_id}: {e}")
        # Если это HTTPException (например, User not found), она будет перехвачена FastAPI
        raise HTTPException(status_code=500, detail="Transaction failed due to server error.")
