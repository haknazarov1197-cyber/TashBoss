import os
import json
import logging
import asyncio
import time
from typing import Optional, Any, Dict, List
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi.middleware.cors import CORSMiddleware
from math import floor # Для надежных расчетов

# --------------------------
# 1. SETUP FIREBASE & LOGGER
# --------------------------

# Environment Variables
FIREBASE_CONFIG_JSON = os.environ.get('FIREBASE_CONFIG')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
APP_ID = os.environ.get('__app_id', 'default-app-id')

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = None

def initialize_firebase():
    """Инициализирует Firebase и Firestore клиент."""
    global db
    try:
        if db is None and FIREBASE_CONFIG_JSON:
            firebase_config = json.loads(FIREBASE_CONFIG_JSON)
            if not firebase_admin._apps:
                cred = credentials.Certificate(firebase_config)
                firebase_admin.initialize_app(cred)
                logger.info("--- Firebase initialized successfully. ---")
            db = firestore.client()
        elif not FIREBASE_CONFIG_JSON:
            logger.error("--- FIREBASE_CONFIG is missing. Firestore will not be available. ---")
    except Exception as e:
        logger.error(f"--- ERROR initializing Firebase: {e} ---")

# --------------------------
# 2. GAME DATA AND SETUP
# --------------------------

# Полный список отраслей (Source of Truth)
INDUSTRIES_LIST = [
    {"id": 1, "frontend_id": "street_cleaning", "name": "Уборка улиц", "description": "Базовая отрасль — чистота и порядок в городе", "base_cost": 100, "base_income": 1, "base_cycle_sec": 60},
    {"id": 2, "frontend_id": "utilities", "name": "Коммунальные службы", "description": "Вода, свет, тепло, благоустройство", "base_cost": 300, "base_income": 3, "base_cycle_sec": 50},
    {"id": 3, "frontend_id": "transport", "name": "Транспорт", "description": "Автобусы, метро, дороги", "base_cost": 1000, "base_income": 8, "base_cycle_sec": 45},
    {"id": 4, "frontend_id": "parks", "name": "Парки и зоны отдыха", "description": "Озеленение, фонтаны, лавочки", "base_cost": 3000, "base_income": 20, "base_cycle_sec": 40},
    {"id": 5, "frontend_id": "small_business", "name": "Малый бизнес", "description": "Кафе, магазины, рынки", "base_cost": 8000, "base_income": 50, "base_cycle_sec": 35},
    {"id": 6, "frontend_id": "factories", "name": "Заводы и фабрики", "description": "Производство и промышленность", "base_cost": 20000, "base_income": 120, "base_cycle_sec": 30},
    {"id": 7, "frontend_id": "air_quality", "name": "Качество воздуха", "description": "Установка фильтров, датчиков, озеленение", "base_cost": 50000, "base_income": 200, "base_cycle_sec": 25},
    {"id": 8, "frontend_id": "it_park", "name": "IT-парк", "description": "Инновации, цифровые стартапы", "base_cost": 120000, "base_income": 500, "base_cycle_sec": 20},
    {"id": 9, "frontend_id": "tourism", "name": "Туризм", "description": "Гостиницы, достопримечательности, фестивали", "base_cost": 250000, "base_income": 1000, "base_cycle_sec": 15},
    {"id": 10, "frontend_id": "international_coop", "name": "Международное сотрудничество", "description": "Привлечение инвестиций и развитие связей с другими странами", "base_cost": 1000000, "base_income": 5000, "base_cycle_sec": 10},
]

# Удобный словарь для быстрого поиска по ЧИСЛОВОМУ ID
INDUSTRIES_DICT_BY_INT_ID = {item['id']: item for item in INDUSTRIES_LIST}

# Удобный словарь для быстрого поиска по СТРОКОВОМУ ID
INDUSTRIES_DICT_BY_FRONTEND_ID = {item['frontend_id']: item for item in INDUSTRIES_LIST}


# Начальное состояние игрока
initial_player_data = {
    "score": 0, # BossCoin (BSS)
    "industries": [], # List of owned industries
    "last_check_time": int(time.time()), # Timestamp of last login/check
    "total_production": 0, # Total income per cycle time (for display)
    "total_income_per_sec": 0.0, # Общий доход в секунду
}


# --------------------------
# 3. SETUP FASTAPI
# --------------------------
app = FastAPI(title="TashBoss Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Гарантирует, что Firestore будет инициализирован до обработки первого запроса."""
    initialize_firebase()

# --------------------------
# 4. HELPER FUNCTIONS
# --------------------------

# --- Firestore Helpers (Async wrapper for synchronous calls) ---

def get_player_doc_ref(user_id: str):
    """Returns the document reference for a player's game state."""
    return db.collection(
        'artifacts', APP_ID, 'users', user_id, 'game_state'
    ).document('player_doc')

def _fetch_data_sync(user_id: str) -> Dict[str, Any]:
    """Synchronous function to fetch or initialize player data."""
    if db is None:
        raise RuntimeError("Firestore is not initialized.")
        
    doc_ref = get_player_doc_ref(user_id)
    doc = doc_ref.get()
    
    if doc.exists:
        data = doc.to_dict()
        # Гарантируем наличие необходимых полей, используя merge
        return {**initial_player_data, **data}
    else:
        # NOTE: Дадим начальный капитал 
        initial_with_score = {**initial_player_data, "score": 1000} 
        doc_ref.set(initial_with_score)
        return initial_with_score

async def get_player_state(user_id: str) -> Dict[str, Any]:
    """Fetches player state asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_data_sync, user_id)

def _save_data_sync(user_id: str, data: Dict):
    """Synchronous function to save data."""
    if db is None:
        raise RuntimeError("Firestore is not initialized.")
        
    doc_ref = get_player_doc_ref(user_id)
    doc_ref.set(data, merge=True)

async def save_player_state(user_id: str, data: Dict):
    """Saves player state asynchronously."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_data_sync, user_id, data)


# --- Game Logic Helper ---

def get_industry_stats(base_data: Dict[str, Any], level: int) -> Dict[str, Any]:
    """Рассчитывает текущий доход, время цикла и стоимость улучшения для отрасли."""
    # Доход: Базовый Доход * Уровень
    current_income = base_data['base_income'] * level
    
    # Время цикла: Базовое Время * (1 - (Уровень - 1) * 0.05)
    # При уровне 1: Базовое Время * (1 - 0) = Базовое Время
    # При уровне 2: Базовое Время * (1 - 0.05) = 95% от Базового Времени
    # Минимальное время цикла: 1 секунда
    cycle_multiplier = 1.0 - (level - 1) * 0.05
    current_cycle_time = max(1, floor(base_data['base_cycle_sec'] * cycle_multiplier))
    
    # Стоимость улучшения: Базовая Стоимость * (Уровень + 1)
    upgrade_cost = base_data['base_cost'] * (level + 1)
    
    # Стоимость покупки (для уровня 1)
    purchase_cost = base_data['base_cost']
    
    # Производство в секунду (для отображения)
    production_per_sec = current_income / current_cycle_time if current_cycle_time > 0 else current_income

    return {
        "current_income": current_income,
        "current_cycle_time": current_cycle_time,
        "upgrade_cost": upgrade_cost,
        "purchase_cost": purchase_cost,
        "production_per_sec": production_per_sec,
    }


def calculate_accumulated_profit(player_state: Dict[str, Any]) -> int:
    """
    Calculates the accumulated profit for all owned industries since the last check
    and updates the player's total production metrics.
    """
    current_time = int(time.time())
    last_check = player_state.get('last_check_time', current_time)
    time_passed = current_time - last_check
    
    total_profit = 0
    total_income_per_sec = 0.0
    
    for owned_industry in player_state.get('industries', []):
        industry_id_int = owned_industry['id']
        level = owned_industry.get('level', 1)
        base_data = INDUSTRIES_DICT_BY_INT_ID.get(industry_id_int)
        
        if not base_data: continue

        stats = get_industry_stats(base_data, level)
        current_income = stats['current_income']
        current_cycle_time = stats['current_cycle_time']
        
        # Расчет прибыли
        if current_cycle_time > 0:
            cycles_completed = int(time_passed / current_cycle_time)
            profit = cycles_completed * current_income
            total_profit += profit
            
            # Расчет общего дохода в секунду
            total_income_per_sec += stats['production_per_sec']

    # Обновляем метрики в стейте игрока
    player_state['total_income_per_sec'] = total_income_per_sec
    
    return total_profit


# --------------------------
# 5. FRONTEND (HTML) ENDPOINT
# --------------------------

# Чтение содержимого index.html (заглушка, будет заменена реальным HTML ниже)
try:
    with open("index.html", "r", encoding="utf-8") as f:
        HTML_CONTENT = f.read()
except FileNotFoundError:
    HTML_CONTENT = "<h1>Error: Mini App HTML file (index.html) not found!</h1>"
    logger.error("index.html was not found.")


@app.get("/", response_class=HTMLResponse)
async def serve_mini_app():
    """Serves the static HTML/JS/CSS file for the Telegram Mini App (the game frontend)."""
    # NOTE: Файл index.html будет сгенерирован ниже. Здесь заглушка для совместимости
    return HTML_CONTENT


@app.get("/master-data")
async def get_master_data():
    """Provides the list of all available industries and costs, including initial stats."""
    # Добавляем рассчитанные стартовые характеристики для фронтенда
    for item in INDUSTRIES_LIST:
        stats = get_industry_stats(item, 1) # Уровень 1
        item['initial_income'] = stats['current_income']
        item['initial_cycle_sec'] = stats['current_cycle_time']
        item['production_per_sec'] = stats['production_per_sec']
    return INDUSTRIES_LIST


# --------------------------
# 6. BOT WEBHOOK ENDPOINT (Пропущено для краткости, так как логика осталась прежней)
# --------------------------

# ... (Оставим заглушку, так как это не основной фокус) ...


# --------------------------
# 7. GAME API ENDPOINTS (with Firestore integration)
# --------------------------

@app.get("/state/{user_id}")
async def get_state(user_id: str):
    """Retrieves the current game state and calculates accumulated profit."""
    try:
        player_state = await get_player_state(user_id)
        
        # Расчет накопленной прибыли и метрик производства
        accumulated_profit = calculate_accumulated_profit(player_state)
        
        # Подготовка данных для фронтенда
        response_data = {
            "user_id": user_id,
            "score": player_state.get('score', 0),
            "industries": player_state.get('industries', []),
            "accumulated_profit": accumulated_profit,
            "total_income_per_sec": player_state.get('total_income_per_sec', 0.0),
            "last_check_time": player_state.get('last_check_time', int(time.time()))
        }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error retrieving player state {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load player state. Error: {e}")


@app.post("/update/{user_id}")
async def update_profit(user_id: str):
    """Collects accumulated profit and resets the timer, returning the new state."""
    try:
        player_state = await get_player_state(user_id)
        
        profit = calculate_accumulated_profit(player_state)
        
        new_score = player_state["score"] + profit
        player_state["score"] = new_score
        player_state["last_check_time"] = int(time.time())
        
        await save_player_state(user_id, player_state)
        
        # Возвращаем полный стейт, как ожидает фронтенд (с 0 накопленной прибыли)
        return {
            "user_id": user_id,
            "score": new_score, 
            "industries": player_state.get('industries', []),
            "accumulated_profit": 0, 
            "total_income_per_sec": player_state.get('total_income_per_sec', 0.0),
            "last_check_time": player_state.get('last_check_time', int(time.time()))
        }

    except Exception as e:
        logger.error(f"Error updating profit for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update profit. Error: {e}")


@app.post("/buy/{user_id}/{industry_id_str}")
async def buy_industry(user_id: str, industry_id_str: str):
    """Allows a player to purchase a new industry (only if not owned)."""
    
    industry_data = INDUSTRIES_DICT_BY_FRONTEND_ID.get(industry_id_str)
    if not industry_data:
        raise HTTPException(status_code=404, detail=f"Industry with ID '{industry_id_str}' not found.")
        
    cost = industry_data['base_cost']
    industry_id_int = industry_data['id']
    
    try:
        player_state = await get_player_state(user_id)
        current_score = player_state["score"]

        if current_score < cost:
            raise HTTPException(status_code=400, detail=f"Not enough BossCoin (BSS). Requires {cost}, available {current_score}.")
        
        # Проверка: куплена ли уже отрасль
        if any(ind['id'] == industry_id_int for ind in player_state["industries"]):
             raise HTTPException(status_code=400, detail="Industry already owned. Use the /upgrade endpoint.")

        # 1. Списание BSS
        new_score = current_score - cost

        # 2. Добавление отрасли (инициализация уровня 1)
        new_industry_instance = {
            "id": industry_id_int,
            "level": 1,
            "is_responsible_assigned": False,
            "industry_name": industry_data['name'],
            "frontend_id": industry_id_str # Добавляем для удобства
        }
        
        player_state["industries"].append(new_industry_instance)
        player_state["score"] = new_score

        # 3. Сохранение
        await save_player_state(user_id, player_state)

        # 4. Перерасчет общей производственной мощности
        calculate_accumulated_profit(player_state)

        # Возвращаем обновленный стейт
        return await get_state(user_id)

    except HTTPException as http_exc:
        raise http_exc
        
    except Exception as e:
        logger.error(f"Error buying industry for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to buy industry. Error: {e}")


@app.post("/upgrade/{user_id}/{industry_id_str}")
async def upgrade_industry(user_id: str, industry_id_str: str):
    """Allows a player to upgrade an existing industry."""
    
    industry_data = INDUSTRIES_DICT_BY_FRONTEND_ID.get(industry_id_str)
    if not industry_data:
        raise HTTPException(status_code=404, detail=f"Industry with ID '{industry_id_str}' not found.")
        
    industry_id_int = industry_data['id']
    
    try:
        player_state = await get_player_state(user_id)
        current_score = player_state["score"]
        
        # 1. Ищем существующую отрасль
        industry_index = next((i for i, ind in enumerate(player_state["industries"]) if ind['id'] == industry_id_int), -1)
        
        if industry_index == -1:
             raise HTTPException(status_code=400, detail="Industry not owned. You must purchase it first.")

        owned_industry = player_state["industries"][industry_index]
        current_level = owned_industry['level']
        
        # 2. Рассчитываем стоимость улучшения
        stats = get_industry_stats(industry_data, current_level)
        upgrade_cost = stats['upgrade_cost']

        if current_score < upgrade_cost:
            raise HTTPException(status_code=400, detail=f"Not enough BossCoin (BSS). Requires {upgrade_cost}, available {current_score}.")
        
        # 3. Применяем улучшение
        new_score = current_score - upgrade_cost
        player_state["score"] = new_score
        player_state["industries"][industry_index]['level'] = current_level + 1

        # 4. Сохранение
        await save_player_state(user_id, player_state)

        # 5. Перерасчет общей производственной мощности
        calculate_accumulated_profit(player_state)

        # Возвращаем обновленный стейт
        return await get_state(user_id)

    except HTTPException as http_exc:
        raise http_exc
        
    except Exception as e:
        logger.error(f"Error upgrading industry for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upgrade industry. Error: {e}")

# ... (Остальные заглушки не нужны, так как я заменил все старые /collect /buy) ...
