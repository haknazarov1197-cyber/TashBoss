import os
import json
import logging
import asyncio
import time

# --- Firebase Imports ---
try:
    import firebase_admin
    from firebase_admin import credentials
    # Импортируем Firestore
    from firebase_admin import firestore 
except ImportError:
    # Убедитесь, что 'firebase-admin' добавлен в requirements.txt
    pass

# --- FastAPI/Uvicorn Imports ---
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel 

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 1. Основное приложение FastAPI (Веб-хук)
# !!! КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: ПЕРЕНОС ИНИЦИАЛИЗАЦИИ В НАЧАЛО !!!
app = FastAPI()

# Глобальные переменные для Firebase
db = None
APP_ID = "tashboss_game" # Ваш уникальный идентификатор приложения

# --- Pydantic Models для запросов ---
class CollectIncomeRequest(BaseModel):
    user_id: int # Telegram ID пользователя
    sector_id: str

# 2. Инициализация Firebase/Firestore
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
            # Инициализируем клиент Firestore
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
        # Если база данных не инициализирована, поднимаем ошибку
        raise Exception("Database not initialized. Check FIREBASE_CREDENTIALS_JSON.")
    
    # Путь: /artifacts/{APP_ID}/public/data/players/{user_id}
    # Используем асинхронный доступ (collection.document(str(user_id)))
    return db.collection(f"artifacts/{APP_ID}/public/data/players").document(str(user_id))

def get_initial_player_data(user_id: int) -> dict:
    """Возвращает начальное состояние игрока."""
    return {
        "user_id": user_id,
        "balance": 1000,
        "last_login": int(time.time()),
        "sectors": {
            "1": {
                "income_per_second": 1,
                "last_collect_time": int(time.time()),
                "level": 1
            }
        },
        "industries_config": {
            "1": {"name": "Базовый Сектор", "base_cost": 100}
        }
    }

# --- Основные API Endpoints ---

@app.head("/")
async def head_root():
    """Обрабатывает HEAD-запросы для проверки активности сервиса (используется Render)."""
    return {"status": "ok"}

@app.get("/")
async def root():
    """Основная конечная точка для проверки работоспособности."""
    return {"message": "Telegram Bot Webhook is running."}

@app.get("/api/load_state")
async def load_state(user_id: int):
    """
    Загружает состояние игрока из Firestore, рассчитывает накопленную прибыль
    и создает новый профиль, если он не существует.
    """
    try:
        player_ref = get_player_ref(user_id)
        # Обратите внимание: метод .get() в firebase-admin асинхронный
        player_doc = player_ref.get()

        if not player_doc.exists:
            # Создаем нового игрока
            initial_data = get_initial_player_data(user_id)
            player_ref.set(initial_data) # set() не асинхронный
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
            
            time_elapsed = current_time - last_collect
            accumulated = time_elapsed * income_per_second
            
            # Обновляем поле для фронтенда (app.js)
            sector["income_to_collect"] = accumulated
            total_accumulated_income += accumulated

        data["total_accumulated_income"] = total_accumulated_income

        return data

    except Exception as e:
        logger.error(f"Ошибка при загрузке состояния игрока {user_id}: {e}")
        # Возвращаем ошибку, которую обработает app.js
        # Нельзя использовать HTTPException, если хотим вернуть JSON с ошибкой
        return {"error": str(e)}

@app.post("/api/collect_income")
async def collect_income(request: CollectIncomeRequest):
    """
    Обрабатывает запрос на сбор дохода с сектора.
    """
    user_id = request.user_id
    sector_id = request.sector_id
    
    if not db:
        # Используем HTTPException для API-ошибок
        raise HTTPException(status_code=500, detail="Database not initialized")

    player_ref = get_player_ref(user_id)
    current_time = int(time.time())
    
    # 1. Транзакция для безопасного обновления
    # NOTE: В python-admin SDK транзакции синхронные, поэтому async/await здесь не используется
    # в самой функции, но конечная точка FastAPI остается асинхронной.
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
        
        # Рассчитываем, сколько прошло времени и сколько собрано
        time_elapsed = current_time - last_collect
        collected_amount = time_elapsed * income_per_second
        
        if collected_amount < 1:
            return {"success": False, "message": "Ещё нет дохода для сбора."}

        # 2. Обновление данных
        new_balance = data.get("balance", 0) + collected_amount
        
        # Обновляем время последнего сбора и баланс
        data["balance"] = new_balance
        data["sectors"][sector_id]["last_collect_time"] = current_time
        
        # 3. Сохраняем транзакцию
        transaction.set(player_ref, data)
        
        return {
            "success": True,
            "new_balance": new_balance,
            "collected": collected_amount
        }

    # Выполняем транзакцию
    try:
        # Вызов синхронной функции внутри асинхронной конечной точки
        # В uvicorn это обычно безопасно, но для чистой асинхронности можно использовать asyncio.to_thread(transaction_update, ...)
        result = transaction_update(db.transaction(), player_ref)
        if result.get("success") is False:
             # Если транзакция вернула ошибку, бросаем HTTP-ответ
             raise HTTPException(status_code=400, detail=result.get("message"))
        return result
    except HTTPException as e:
        # Пробрасываем HTTP-ошибки
        raise e
    except Exception as e:
        logger.error(f"Ошибка транзакции сбора дохода для {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера при сборе дохода.")
