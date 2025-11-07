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
from fastapi.middleware.cors import CORSMiddleware # Добавляем для безопасности

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
        if db is None and FIREBASE_CONFIG_JSON: # Проверяем, чтобы не инициализировать повторно
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

# Удаляем глобальный вызов initialize_firebase(), 
# чтобы избежать ошибки "Firestore is not initialized."


# --------------------------
# 2. GAME DATA AND SETUP
# --------------------------

# Полный список отраслей (Source of Truth)
INDUSTRIES_LIST = [
    {"id": 1, "name": "Уборка улиц", "description": "Базовая отрасль — чистота и порядок в городе", "base_cost": 100, "base_income": 1, "cycle_time_sec": 60},
    {"id": 2, "name": "Коммунальные службы", "description": "Вода, свет, тепло, благоустройство", "base_cost": 300, "base_income": 3, "cycle_time_sec": 50},
    {"id": 3, "name": "Транспорт", "description": "Автобусы, метро, дороги", "base_cost": 1000, "base_income": 8, "cycle_time_sec": 45},
    {"id": 4, "name": "Парки и зоны отдыха", "description": "Озеленение, фонтаны, лавочки", "base_cost": 3000, "base_income": 20, "cycle_time_sec": 40},
    {"id": 5, "name": "Малый бизнес", "description": "Кафе, магазины, рынки", "base_cost": 8000, "base_income": 50, "cycle_time_sec": 35},
    {"id": 6, "name": "Заводы и фабрики", "description": "Производство и промышленность", "base_cost": 20000, "base_income": 120, "cycle_time_sec": 30},
    {"id": 7, "name": "Качество воздуха", "description": "Установка фильтров, датчиков, озеленение", "base_cost": 50000, "base_income": 200, "cycle_time_sec": 25},
    {"id": 8, "name": "IT-парк", "description": "Инновации, цифровые стартапы", "base_cost": 120000, "base_income": 500, "cycle_time_sec": 20},
    {"id": 9, "name": "Туризм", "description": "Гостиницы, достопримечательности, фестивали", "base_cost": 250000, "base_income": 1000, "cycle_time_sec": 15},
    {"id": 10, "name": "Международное сотрудничество", "description": "Привлечение инвестиций и развитие связей с другими странами", "base_cost": 1000000, "base_income": 5000, "cycle_time_sec": 10},
]
# Удобный словарь для быстрого поиска
INDUSTRIES_DICT = {item['id']: item for item in INDUSTRIES_LIST}

# Начальное состояние игрока
initial_player_data = {
    "score": 0, # BossCoin (BSS)
    "industries": [], # List of owned industries
    "last_check_time": int(time.time()), # Timestamp of last login/check
    "total_production": 0, # Total income per cycle time (for display)
}


# --------------------------
# 3. SETUP FASTAPI
# --------------------------
app = FastAPI(title="TashBoss Bot API")

# FIX: Используем CORS middleware, чтобы Mini App мог обращаться к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# FIX: Гарантируем, что инициализация Firebase произойдет при запуске сервера
@app.on_event("startup")
async def startup_event():
    """Гарантирует, что Firestore будет инициализирован до обработки первого запроса."""
    initialize_firebase()

# --------------------------
# 4. HELPER FUNCTIONS
# --------------------------

# --- Telegram Helper ---

def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    """Sends a message back to the Telegram user."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. Cannot send message.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent to chat {chat_id}. Status: {response.status_code}")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"Telegram API HTTP error: {e}. Response: {response.text}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending message to Telegram: {e}")
        return False

# --- Firestore Helpers (Async wrapper for synchronous calls) ---

def get_player_doc_ref(user_id: str):
    """Returns the document reference for a player's game state."""
    # Используем стандартный путь для приватных данных с учетом APP_ID
    return db.collection(
        'artifacts', APP_ID, 'users', user_id, 'game_state'
    ).document('player_doc')

def _fetch_data_sync(user_id: str) -> Dict[str, Any]:
    """Synchronous function to fetch or initialize player data."""
    if db is None:
        # Теперь это должно срабатывать только если initialize_firebase() провалилась
        raise RuntimeError("Firestore is not initialized.")
        
    doc_ref = get_player_doc_ref(user_id)
    doc = doc_ref.get()
    
    if doc.exists:
        data = doc.to_dict()
        # Гарантируем наличие необходимых полей, используя merge
        return {**initial_player_data, **data}
    else:
        # Initialize new player
        # NOTE: Дадим начальный капитал, чтобы можно было сразу что-то купить.
        initial_with_score = {**initial_player_data, "score": 500}
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

def calculate_accumulated_profit(player_state: Dict[str, Any]) -> int:
    """
    Calculates the accumulated profit for all owned industries since the last check.
    """
    current_time = int(time.time())
    last_check = player_state.get('last_check_time', current_time)
    time_passed = current_time - last_check
    
    total_profit = 0
    total_production_per_cycle = 0
    
    for owned_industry in player_state.get('industries', []):
        industry_id = owned_industry['id']
        base_data = INDUSTRIES_DICT.get(industry_id)
        if not base_data:
            logger.warning(f"Industry with ID {industry_id} not found in master list.")
            continue

        # Текущие характеристики отрасли (уровень, доход, время цикла)
        level = owned_industry.get('level', 1)
        current_income = base_data['base_income'] * level 
        current_cycle_time = base_data['cycle_time_sec']
        
        # Расчет прибыли
        if current_cycle_time > 0:
            cycles_completed = int(time_passed / current_cycle_time)
            profit = cycles_completed * current_income
            total_profit += profit
            total_production_per_cycle += current_income 
            
    # Сохраняем общую производственную мощность для отображения
    player_state['total_production'] = total_production_per_cycle
    
    return total_profit

# --------------------------
# 5. FRONTEND (HTML) ENDPOINT
# --------------------------

# Чтение содержимого index.html
try:
    with open("index.html", "r", encoding="utf-8") as f:
        HTML_CONTENT = f.read()
except FileNotFoundError:
    HTML_CONTENT = "<h1>Error: Mini App HTML file (index.html) not found!</h1>"
    logger.error("index.html was not found.")

@app.get("/", response_class=HTMLResponse)
async def serve_mini_app():
    """Serves the static HTML/JS/CSS file for the Telegram Mini App (the game frontend)."""
    return HTML_CONTENT

@app.get("/master-data")
async def get_master_data():
    """Provides the list of all available industries and costs."""
    return INDUSTRIES_LIST


# --------------------------
# 6. BOT WEBHOOK ENDPOINT
# --------------------------

@app.post("/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request):
    """
    Handles incoming updates from Telegram and processes commands.
    """
    try:
        update = await request.json()
        
        if 'message' not in update:
            return JSONResponse({"status": "ok", "message": "No message in update"}, status_code=200)

        message = update['message']
        chat_id = message['chat']['id']
        text = message.get('text', '')
        
        logger.info(f"Received message from chat {chat_id}: {text}")

        # Check for the /start command
        if text.startswith('/start'):
            welcome_text = (
                "Привет, босс! 👋 Добро пожаловать в **TashBoss**.\n\n"
                "Валюта: **BossCoin (BSS)**.\n"
                "Начните с покупки первой отрасли, чтобы создать свой город!"
            )
            
            # MINI_APP_URL should be set to your Render URL (e.g., https://tashboss.onrender.com)
            mini_app_url = os.environ.get('MINI_APP_URL', 'https://tashboss.onrender.com') 
            
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🏗️ Запустить TashBoss",
                            "web_app": {"url": mini_app_url}
                        }
                    ]
                ]
            }

            send_message(chat_id, welcome_text, reply_markup=reply_markup)
        
        # Заглушка для других команд, чтобы не возвращать 404
        elif text.startswith('/'):
            send_message(chat_id, "Неизвестная команда. Введите /start для начала игры.")
            
        return JSONResponse({"status": "ok"}, status_code=200)

    except Exception as e:
        logger.error(f"Error processing Telegram webhook: {e}")
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=200)


# --------------------------
# 7. GAME API ENDPOINTS (with Firestore integration)
# --------------------------

@app.get("/state/{user_id}")
async def get_state(user_id: str):
    """
    Retrieves the current game state for a user from Firestore. 
    Also calculates and returns the accumulated profit since the last check.
    """
    try:
        player_state = await get_player_state(user_id)
        
        # Расчет накопленной прибыли
        accumulated_profit = calculate_accumulated_profit(player_state)
        
        # Подготовка данных для фронтенда
        response_data = {
            "score": player_state.get('score', 0),
            "industries": player_state.get('industries', []),
            "accumulated_profit": accumulated_profit,
            "total_production": player_state.get('total_production', 0),
            "last_check_time": player_state.get('last_check_time', int(time.time()))
        }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error retrieving player state {user_id}: {e}")
        # Проверяем на ошибку инициализации DB, чтобы дать более точный ответ
        if "Firestore is not initialized" in str(e):
            raise HTTPException(
                status_code=500, 
                detail="Database initialization error. Please try again in a few seconds."
            )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to load player state from Firestore. Error: {e}"
        )

@app.post("/collect/{user_id}")
async def collect_profit(user_id: str):
    """Collects accumulated profit and resets the timer."""
    try:
        player_state = await get_player_state(user_id)
        
        # 1. Расчет прибыли
        profit = calculate_accumulated_profit(player_state)
        
        if profit == 0:
            return JSONResponse({
                "score": player_state.get('score', 0),
                "collected": 0,
                "message": "Нет накопленной прибыли для сбора."
            })
            
        # 2. Обновление счета и времени
        new_score = player_state["score"] + profit
        player_state["score"] = new_score
        player_state["last_check_time"] = int(time.time())
        
        # 3. Сохранение
        await save_player_state(user_id, player_state)
        
        return {
            "score": new_score, 
            "collected": profit, 
            "message": f"Собрано {profit} BSS."
        }

    except Exception as e:
        logger.error(f"Error collecting profit for {user_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to collect profit. Error: {e}"
        )

@app.post("/buy/{user_id}/{industry_id}")
async def buy_industry(user_id: str, industry_id: int):
    """Allows a player to purchase a new industry."""
    
    industry_data = INDUSTRIES_DICT.get(industry_id)
    if not industry_data:
        raise HTTPException(status_code=404, detail="Industry not found.")
        
    cost = industry_data['base_cost']
    
    try:
        player_state = await get_player_state(user_id)
        current_score = player_state["score"]

        # Проверка, достаточно ли денег
        if current_score < cost:
            raise HTTPException(
                status_code=400, 
                detail=f"Not enough BossCoin (BSS). Requires {cost}, available {current_score}."
            )
        
        # Проверка, не куплена ли уже отрасль
        if any(ind['id'] == industry_id for ind in player_state["industries"]):
             raise HTTPException(
                status_code=400, 
                detail="Industry already owned. Upgrades are not yet implemented."
            )

        # 1. Списание BSS
        new_score = current_score - cost

        # 2. Добавление отрасли (инициализация уровня)
        new_industry_instance = {
            "id": industry_id,
            "level": 1,
            "is_responsible_assigned": False, 
            "industry_name": industry_data['name'] 
        }
        
        player_state["industries"].append(new_industry_instance)
        player_state["score"] = new_score

        # 3. Сохранение
        await save_player_state(user_id, player_state)

        # 4. Перерасчет общей производственной мощности
        calculate_accumulated_profit(player_state)

        return {
            "score": new_score, 
            "new_industry": new_industry_instance,
            "total_production": player_state.get('total_production', 0)
        }

    except HTTPException as http_exc:
        raise http_exc
        
    except Exception as e:
        logger.error(f"Error buying industry for {user_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to buy industry. Error: {e}"
        )

# --------------------------
# 8. REMOVING OLD PLACEHOLDERS
# --------------------------

@app.get("/state")
def remove_old_state():
    raise HTTPException(status_code=404, detail="Use /state/{user_id} endpoint instead.")

@app.post("/tap")
def remove_old_tap():
    raise HTTPException(status_code=404, detail="Use /collect/{user_id} endpoint instead.")

@app.post("/upgrade")
def remove_old_upgrade():
    raise HTTPException(status_code=404, detail="Use /buy/{user_id}/{industry_id} for purchasing industries instead.")
