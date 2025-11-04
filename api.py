import os
import json
import time
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any

# --- ИМПОРТ И ИНИЦИАЛИЗАЦИЯ FIREBASE ---
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    HAS_FIREBASE = True
except ImportError:
    # Заглушка для случая, когда firebase-admin не установлен локально
    HAS_FIREBASE = False

db = None
auth = None
firebase_config = None
app_id = "tashboss-app" 

# Глобальные переменные Canvas (предполагаем, что они передаются из окружения)
try:
    if '__firebase_config' in globals():
        firebase_config = json.loads(globals().get('__firebase_config', '{}'))
    if '__app_id' in globals():
        app_id = globals().get('__app_id', 'tashboss-app')
except Exception:
    pass # Продолжаем с локальными заглушками

if HAS_FIREBASE and firebase_config and firebase_config != {}:
    try:
        # Проверка, была ли уже инициализация
        if not firebase_admin._apps:
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("Firestore client initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize Firebase Admin SDK: {e}")
        HAS_FIREBASE = False


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- КОНФИГУРАЦИЯ ИГРЫ (ОТРАСЛИ) ---
# Полный список отраслей по вашему плану
INDUSTRIES_CONFIG = {
    "1": {"name": "Уборка улиц", "base_income": 1, "base_cost": 100, "base_cycle_time": 60},
    "2": {"name": "Коммунальные службы", "base_income": 3, "base_cost": 300, "base_cycle_time": 50},
    "3": {"name": "Транспорт", "base_income": 8, "base_cost": 1000, "base_cycle_time": 45},
    "4": {"name": "Парки и зоны отдыха", "base_income": 20, "base_cost": 3000, "base_cycle_time": 40},
    "5": {"name": "Малый бизнес", "base_income": 50, "base_cost": 8000, "base_cycle_time": 35},
    "6": {"name": "Заводы и фабрики", "base_income": 120, "base_cost": 20000, "base_cycle_time": 30},
    "7": {"name": "Качество воздуха", "base_income": 200, "base_cost": 50000, "base_cycle_time": 25},
    "8": {"name": "IT-парк", "base_income": 500, "base_cost": 120000, "base_cycle_time": 20},
    "9": {"name": "Туризм", "base_income": 1000, "base_cost": 250000, "base_cycle_time": 15},
    "10": {"name": "Международное сотрудничество", "base_income": 5000, "base_cost": 1000000, "base_cycle_time": 10},
}

# --- УТИЛИТЫ FIREBASE ---

def get_player_doc(user_id: str):
    """Получает ссылку на документ пользователя в Firestore."""
    if not db:
        return None
    # Используем путь для приватных данных
    return db.collection("artifacts").document(app_id).collection("users").document(user_id).collection("game_data").document("player_state")


def get_default_player_state():
    """Возвращает начальное состояние игрока."""
    current_time = int(time.time())
    
    initial_sectors = {}
    for k in INDUSTRIES_CONFIG:
        # Для начала, пусть первый сектор будет куплен
        initial_level = 1 if k == "1" else 0
        initial_sectors[k] = {
            "level": initial_level, 
            "last_collect_time": current_time,
            "is_responsible_assigned": False
        }

    return {
        "balance": 100, # Начальный баланс
        "sectors": initial_sectors,
        "created_at": current_time
    }

# --- ОСНОВНАЯ ЛОГИКА ИГРЫ ---

def get_sector_params(sector_id: str, level: int) -> Dict[str, Any]:
    """
    Рассчитывает текущий доход, время цикла и стоимость улучшения 
    на основе уровня сектора.
    """
    config = INDUSTRIES_CONFIG.get(sector_id)
    if not config:
        return None

    # 1. Доход: линейный рост
    income_per_cycle = config["base_income"] * level

    # 2. Время цикла: уменьшается на 0.5 секунды за уровень, но не более чем на 50%
    base_time = config["base_cycle_time"]
    time_reduction = (level - 1) * 0.5
    max_reduction = base_time / 2 
    
    current_cycle_time = max(base_time - time_reduction, max_reduction)
    
    # 3. Стоимость улучшения: экспоненциальный рост
    # Уровень N стоит BaseCost * (N^1.2)
    next_level = level + 1
    cost = int(config["base_cost"] * (next_level ** 1.2))

    return {
        "income_per_cycle": income_per_cycle,
        "cycle_time": current_cycle_time,
        "next_upgrade_cost": cost,
        "base_cost": config["base_cost"] # Для расчета покупки
    }

def calculate_income_and_time(player_state: Dict[str, Any], sector_id: str) -> Dict[str, Any]:
    """
    Рассчитывает накопленный доход и определяет новое время последнего сбора.
    """
    sector = player_state["sectors"].get(sector_id)
    
    if not sector or sector["level"] == 0:
        return {"income": 0, "new_last_collect_time": sector["last_collect_time"] if sector else int(time.time())}

    # Получаем текущие параметры на основе уровня
    params = get_sector_params(sector_id, sector["level"])
    
    income_per_cycle = params["income_per_cycle"]
    cycle_time = params["cycle_time"]

    current_time = int(time.time())
    
    idle_time = current_time - sector["last_collect_time"]
    
    # Рассчитываем количество полных циклов
    cycles_passed = int(idle_time // cycle_time)
    income_to_collect = cycles_passed * income_per_cycle
    
    # Обновляем время последнего сбора, чтобы не потерять остаток времени (idle_time % cycle_time)
    new_last_collect_time = sector["last_collect_time"] + (cycles_passed * cycle_time)

    if cycles_passed == 0:
         new_last_collect_time = sector["last_collect_time"]

    return {
        "income": income_to_collect,
        "new_last_collect_time": new_last_collect_time,
        "current_cycle_time": cycle_time
    }

# --- API ENDPOINTS (Для Web App) ---

# В этом файле я не включаю HTML, так как вы, вероятно, обслуживаете его отдельно 
# или он уже был встроен в ваш развернутый API. 
# Я предполагаю, что конечная точка /webapp в вашей развернутой системе работает.

@app.get("/api/load_state")
async def load_state(user_id: str):
    """
    Загружает состояние игрока из базы данных и рассчитывает накопленную прибыль.
    """
    if not HAS_FIREBASE or not db:
        return JSONResponse({"error": "Database not initialized. Cannot load state."}, status_code=500)

    doc_ref = get_player_doc(user_id)
    doc = doc_ref.get()

    if doc.exists:
        player_state = doc.to_dict()
    else:
        # Создание нового игрока
        player_state = get_default_player_state()
        doc_ref.set(player_state) 

    # Рассчитываем накопленную прибыль для всех секторов
    accumulated_income = 0
    sectors_data_for_app = {}

    for sector_id, sector_data in player_state["sectors"].items():
        current_level = sector_data["level"]
        params = get_sector_params(sector_id, current_level)
        
        # Если куплено (level > 0)
        if current_level > 0:
            result = calculate_income_and_time(player_state, sector_id)
            sector_data["income_to_collect"] = result["income"]
            sector_data["current_cycle_time"] = result["current_cycle_time"]
            accumulated_income += result["income"]
        else:
            # Если не куплено (level = 0)
            sector_data["income_to_collect"] = 0
            sector_data["current_cycle_time"] = INDUSTRIES_CONFIG[sector_id]["base_cycle_time"]

        # Стоимость: Если level > 0, то берем следующую стоимость; если level = 0, то берем base_cost
        if current_level > 0:
            sector_data["next_upgrade_cost"] = params["next_upgrade_cost"]
            sector_data["income_per_cycle"] = params["income_per_cycle"]
        else:
            sector_data["next_upgrade_cost"] = params["base_cost"]
            sector_data["income_per_cycle"] = params["income_per_cycle"] # будет base_income * 0 = 0, но это ок

        sectors_data_for_app[sector_id] = sector_data


    # Обновляем состояние игрока для отправки в Mini App
    player_state["sectors"] = sectors_data_for_app
    player_state["total_accumulated_income"] = accumulated_income
    
    # Отправляем полный список секторов (включая имена) для рендеринга
    player_state["industries_config"] = INDUSTRIES_CONFIG 

    return JSONResponse(player_state)

@app.post("/api/collect_income")
async def collect_income(request: Request):
    """
    Обрабатывает запрос на сбор дохода от Web App.
    """
    if not HAS_FIREBASE or not db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)

    try:
        data = await request.json()
        user_id = str(data.get("user_id"))
        sector_id = str(data.get("sector_id"))
    except Exception:
        return JSONResponse({"error": "Invalid request format"}, status_code=400)

    doc_ref = get_player_doc(user_id)
    
    # Используем транзакцию для безопасного обновления
    @firestore.transactional
    def update_in_transaction(transaction, doc_ref):
        doc = doc_ref.get(transaction=transaction)
        
        if not doc.exists:
            return {"success": False, "message": "User state not found"}

        player_state = doc.to_dict()
        sector_data = player_state["sectors"].get(sector_id)
        
        if not sector_data or sector_data["level"] == 0:
            return {"success": False, "message": "Sector not purchased or not found"}

        # 1. Рассчитываем, сколько можно собрать
        result = calculate_income_and_time(player_state, sector_id)
        income_to_collect = result["income"]
        new_last_collect_time = result["new_last_collect_time"]

        if income_to_collect > 0:
            # 2. Обновляем баланс и время сбора в транзакции
            player_state["balance"] += income_to_collect
            player_state["sectors"][sector_id]["last_collect_time"] = new_last_collect_time

            # 3. Сохраняем обновленное состояние
            transaction.set(doc_ref, player_state)

            return {
                "success": True, 
                "collected": income_to_collect, 
                "new_balance": player_state["balance"]
            }
        else:
            return {"success": False, "message": "No income ready to collect"}

    try:
        transaction = db.transaction()
        result = update_in_transaction(transaction, doc_ref)
        if result.get("success") is True:
             return JSONResponse(result, status_code=200)
        elif result.get("success") is False and result.get("message") == "No income ready to collect":
             return JSONResponse(result, status_code=200)
        else:
             return JSONResponse(result, status_code=400)

    except Exception as e:
        print(f"Transaction error (collect): {e}")
        return JSONResponse({"error": f"Transaction failed: {e}"}, status_code=500)

@app.post("/api/upgrade_sector")
async def upgrade_sector(request: Request):
    """
    Обрабатывает запрос на улучшение сектора.
    """
    if not HAS_FIREBASE or not db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)

    try:
        data = await request.json()
        user_id = str(data.get("user_id"))
        sector_id = str(data.get("sector_id"))
    except Exception:
        return JSONResponse({"error": "Invalid request format"}, status_code=400)

    doc_ref = get_player_doc(user_id)
    
    @firestore.transactional
    def update_in_transaction(transaction, doc_ref):
        doc = doc_ref.get(transaction=transaction)

        if not doc.exists:
            return {"success": False, "message": "User state not found"}

        player_state = doc.to_dict()
        sector_data = player_state["sectors"].get(sector_id)
        
        if not sector_data:
            return {"success": False, "message": "Sector not found"}

        current_level = sector_data["level"]
        
        # Получаем параметры для расчета стоимости
        params = get_sector_params(sector_id, current_level)
        
        # Определяем стоимость: если уровень 0, это покупка; иначе - улучшение
        cost = params["base_cost"] if current_level == 0 else params["next_upgrade_cost"]

        # Проверка баланса
        if player_state["balance"] < cost:
            return {"success": False, "message": "Insufficient balance"}

        # 1. Проводим транзакцию
        player_state["balance"] -= cost
        player_state["sectors"][sector_id]["level"] += 1
        
        # При первой покупке (level 0 -> 1), устанавливаем время сбора
        if current_level == 0:
             player_state["sectors"][sector_id]["last_collect_time"] = int(time.time())

        # 2. Сохраняем обновленное состояние в Firestore
        transaction.set(doc_ref, player_state)

        return {
            "success": True, 
            "new_level": player_state["sectors"][sector_id]["level"],
            "new_balance": player_state["balance"],
            "cost": cost
        }

    try:
        transaction = db.transaction()
        result = update_in_transaction(transaction, doc_ref)
        if result.get("success") is True:
             return JSONResponse(result, status_code=200)
        else:
             return JSONResponse(result, status_code=400)
    except Exception as e:
        print(f"Transaction error (upgrade): {e}")
        return JSONResponse({"error": f"Transaction failed: {e}"}, status_code=500)
