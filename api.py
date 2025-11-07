import os
import json
import asyncio
from typing import Dict
from fastapi import FastAPI, HTTPException
# Для работы с firebase-admin необходимо, чтобы библиотека была установлена
from firebase_admin import initialize_app, firestore, credentials 

# --- КОНФИГУРАЦИЯ FIREBASE ---
# Переменные, предоставленные средой Canvas
FIREBASE_CONFIG_JSON = os.environ.get('__firebase_config')
APP_ID = os.environ.get('__app_id', 'default-app-id') 

db = None
app = None
API_INITIALIZED = False

initial_player_data = {
    "score": 0,
    "clicks_per_tap": 1
}

def initialize_firebase():
    """Инициализирует Firebase/Firestore."""
    global db, app, API_INITIALIZED
    
    if API_INITIALIZED:
        return

    print("--- Попытка инициализации Firebase ---")
    if not FIREBASE_CONFIG_JSON:
        print("ОШИБКА: Переменная __firebase_config не найдена.")
        raise RuntimeError("Firebase config не предоставлен в среде.")

    try:
        config_data = json.loads(FIREBASE_CONFIG_JSON)
        cred = credentials.Certificate(config_data)
        
        # Инициализация приложения Firebase
        app = initialize_app(cred)
        db = firestore.client()
        API_INITIALIZED = True
        
        # КРИТИЧНО ВАЖНЫЙ ВЫВОД: Project ID
        project_id = config_data.get('project_id', 'НЕИЗВЕСТЕН')
        print(f"--- ИСПОЛЬЗУЕМЫЙ PROJECT ID FIREBASE: {project_id} ---")
        print(f"УСПЕХ: Firebase инициализирован. ID приложения: {APP_ID}")
        
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать Firebase. {e}")
        # Вызываем исключение, чтобы сервер упал, если инициализация не удалась
        raise RuntimeError(f"Не удалось инициализировать Firestore: {e}")

# Инициализируем FastAPI и Firebase
app = FastAPI()
initialize_firebase()


# --- Вспомогательные функции для Firestore с асинхронной оберткой ---
# Эти функции используют run_in_executor для корректного выполнения синхронных 
# вызовов Firestore внутри асинхронной среды FastAPI.

def get_player_doc_ref(user_id: str):
    """Возвращает ссылку на документ игрока."""
    return db.collection(
        'artifacts', APP_ID, 'users', user_id, 'game_state'
    ).document('player_doc')

def _fetch_data_sync(user_id: str):
    """Синхронная функция получения данных (для выполнения в Executor)."""
    doc_ref = get_player_doc_ref(user_id)
    doc = doc_ref.get()
    
    if doc.exists:
        return doc.to_dict()
    else:
        # Инициализация нового игрока (тоже синхронный вызов)
        doc_ref.set(initial_player_data)
        return initial_player_data

async def get_player_state(user_id: str) -> Dict:
    """Получает состояние игрока, выполняя синхронный вызов асинхронно."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_data_sync, user_id)

def _save_data_sync(user_id: str, data: Dict):
    """Синхронная функция сохранения данных."""
    doc_ref = get_player_doc_ref(user_id)
    doc_ref.set(data)

async def save_player_state(user_id: str, data: Dict):
    """Сохраняет состояние игрока, выполняя синхронный вызов асинхронно."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_data_sync, user_id, data)


# --- ЭНДПОИНТЫ API ---

@app.get("/")
async def health_check():
    """Проверка здоровья API."""
    return {"message": "Cosmic Clicker API запущен и готов к работе."}

@app.get("/state/{user_id}")
async def get_state(user_id: str):
    """Получает текущее состояние игрока."""
    try:
        state = await get_player_state(user_id)
        return state
    except Exception as e:
        print(f"Ошибка при получении состояния игрока {user_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Не удалось загрузить состояние игрока из Firestore. Ошибка: {e}"
        )

@app.post("/tap/{user_id}")
async def tap_action(user_id: str):
    """Обрабатывает клик игрока и обновляет счет."""
    try:
        current_state = await get_player_state(user_id)
        clicks_per_tap = current_state.get("clicks_per_tap", 1)
        
        new_score = current_state["score"] + clicks_per_tap
        current_state["score"] = new_score
        
        await save_player_state(user_id, current_state)
        
        return {"new_score": new_score, "clicks_per_tap": clicks_per_tap}

    except Exception as e:
        print(f"Ошибка при обработке клика для {user_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Не удалось сохранить клик. Ошибка: {e}"
        )

@app.post("/upgrade/{user_id}")
async def buy_upgrade(user_id: str):
    """Обрабатывает покупку улучшения (увеличение clicks_per_tap)."""
    UPGRADE_COST = 100
    
    try:
        current_state = await get_player_state(user_id)
        current_score = current_state["score"]
        current_cpt = current_state["clicks_per_tap"]

        if current_score < UPGRADE_COST:
            raise HTTPException(
                status_code=400, 
                detail=f"Недостаточно очков. Требуется {UPGRADE_COST}, доступно {current_score}."
            )

        new_score = current_score - UPGRADE_COST
        new_cpt = current_cpt + 1

        current_state["score"] = new_score
        current_state["clicks_per_tap"] = new_cpt

        await save_player_state(user_id, current_state)

        return {"new_score": new_score, "new_clicks_per_tap": new_cpt}

    except HTTPException as http_exc:
        raise http_exc
        
    except Exception as e:
        print(f"Ошибка при покупке улучшения для {user_id}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Не удалось купить улучшение. Ошибка: {e}"
        )
    
