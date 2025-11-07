import os
import sys
import json
import logging
import asyncio
import time
from typing import List, Optional, Dict, Any

# Импорт Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Настройка констант для Firestore ---
# ВАЖНО: Эти значения должны быть получены динамически в реальном приложении.
# Используем заглушки, как и в вашем коде.
APP_ID = 'telegram_clicker_app_id' # Идентификатор вашего приложения/игры
COLLECTION_NAME = 'player_state' # Коллекция для хранения состояния игроков

# --- Глобальные переменные Firebase ---
db = None
DEFAULT_STARTING_SCORE = 0
DEFAULT_CLICKS_PER_TAP = 1

# --- Модели Pydantic для данных ---

class PlayerState(BaseModel):
    """Модель состояния игрока для кликера."""
    user_id: str = Field(..., description="Уникальный ID пользователя Telegram.")
    score: int = Field(DEFAULT_STARTING_SCORE, description="Текущее количество очков игрока.")
    clicks_per_tap: int = Field(DEFAULT_CLICKS_PER_TAP, description="Количество очков за одно нажатие.")
    last_login: Optional[float] = Field(None, description="Временная метка последнего входа/активности.")

class TapResponse(BaseModel):
    """Ответ после клика."""
    new_score: int
    clicks_per_tap: int

# --- Функции инициализации Firebase ---

def init_firebase():
    """Инициализирует Firebase Admin SDK."""
    global db
    logging.info("Запуск инициализации Firebase...")

    try:
        # Загрузка учетных данных
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if not service_account_json:
            logging.critical("Переменная окружения FIREBASE_SERVICE_ACCOUNT_JSON не установлена.")
            # Для запуска в среде, где нет переменных окружения, можно вернуть None
            return 

        # Парсинг JSON
        creds_dict = json.loads(service_account_json)
        
        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Замена экранированных '\n' на реальные символы новой строки
        private_key = creds_dict.get('private_key')
        if private_key and isinstance(private_key, str):
            creds_dict['private_key'] = private_key.replace(r'\n', '\n')
            logging.info("Успешно исправлены экранированные символы новой строки в 'private_key'.")

        # Создание учетных данных и инициализация приложения
        cred = credentials.Certificate(creds_dict)
        try:
            initialize_app(cred)
            logging.info("✅ Инициализация Firebase успешно завершена.")
        except ValueError as e:
            if "already exists" in str(e):
                logging.info("Приложение Firebase уже инициализировано, продолжаем.")
            else:
                raise e
        
        # Получение экземпляра Firestore
        db = firestore.client()
        logging.info("Экземпляр Firestore доступен.")

    except Exception as e:
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Детали: {e}")
        # Не выходим, чтобы API мог работать, но DB будет недоступен
        db = None


def get_player_doc_ref(user_id: str):
    """
    Возвращает ссылку на документ состояния игрока.
    Путь: /artifacts/{APP_ID}/{COLLECTION_NAME}/{user_id}
    Или, используя вашу структуру: /artifacts/{APP_ID}/users/{user_id}/player_state/data
    Для простоты сделаем PlayerState документом, а не коллекцией.
    Путь: /artifacts/{APP_ID}/player_state/{user_id}
    """
    if db is None:
        return None
    
    # Используем более простую и логичную структуру для данных игрока
    return db.collection('artifacts').document(APP_ID).collection(COLLECTION_NAME).document(user_id)

# --- Настройка FastAPI ---

app = FastAPI(title="Telegram Clicker Game API (FastAPI + Firestore)")

# Настройка CORS (нужно для Telegram Mini App, работающего на стороннем домене)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем все источники, так как Mini App запускается с динамического URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Вызывается при запуске приложения."""
    logging.info("Начало этапа lifespan: Запуск функции init_firebase...")
    await asyncio.to_thread(init_firebase)


# --- Пути API (Endpoints) ---

@app.get("/", status_code=status.HTTP_200_OK)
async def health_check():
    """Проверка состояния сервиса и доступности DB."""
    if db is None:
         # Возвращаем 503, если Firebase не инициализирован
         raise HTTPException(
             status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
             detail="API is operational, but Firebase Admin SDK failed to initialize."
         )
    try:
        # Простой тест доступности Firestore (получение не существующего пути)
        db.collection("health_check").document("test").get()
        return {"status": "ok", "message": "API is operational and Firebase connected."}
    except Exception as e:
        logging.error(f"Health check failed due to Firestore connection error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"API is operational but Firestore connection failed during check: {e}"
        )


@app.get("/state/{user_id}", response_model=PlayerState)
async def get_player_state(user_id: str):
    """Получает текущее состояние игрока по его ID. Если игрок не найден, создает начальное состояние."""
    player_ref = get_player_doc_ref(user_id)
    if player_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    try:
        doc = player_ref.get()
        current_time = time.time()
        
        if doc.exists:
            # Игрок найден, обновляем время последнего входа
            state_data = doc.to_dict()
            player_ref.update({"last_login": current_time})
            return PlayerState(user_id=user_id, **state_data)
        else:
            # Игрок не найден, создаем новое начальное состояние
            new_state = PlayerState(
                user_id=user_id,
                score=DEFAULT_STARTING_SCORE,
                clicks_per_tap=DEFAULT_CLICKS_PER_TAP,
                last_login=current_time
            )
            player_ref.set(new_state.model_dump())
            logging.info(f"Создано новое состояние для пользователя {user_id}")
            return new_state

    except Exception as e:
        logging.error(f"Ошибка получения/создания состояния игрока {user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось получить или создать состояние игрока.")


@app.post("/tap/{user_id}", response_model=TapResponse)
async def handle_tap(user_id: str):
    """
    Обрабатывает клик от пользователя, атомарно обновляет счет в Firestore.
    Использует `update` с транзакцией или инкрементом для безопасности.
    """
    player_ref = get_player_doc_ref(user_id)
    if player_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    try:
        # Используем транзакцию для атомарного обновления, чтобы избежать гонки данных
        
        @firestore.transactional
        def update_in_transaction(transaction, player_ref):
            """Функция транзакции: читает, обновляет, записывает."""
            snapshot = player_ref.get(transaction=transaction)
            
            if not snapshot.exists:
                # Если игрок не существует, создаем его (это должно быть предотвращено GET запросом, но на всякий случай)
                new_state = PlayerState(
                    user_id=user_id,
                    score=DEFAULT_STARTING_SCORE,
                    clicks_per_tap=DEFAULT_CLICKS_PER_TAP,
                    last_login=time.time()
                )
                transaction.set(player_ref, new_state.model_dump())
                current_score = new_state.score
                clicks_per_tap = new_state.clicks_per_tap
            else:
                state_data = snapshot.to_dict()
                clicks_per_tap = state_data.get('clicks_per_tap', DEFAULT_CLICKS_PER_TAP)
                current_score = state_data.get('score', DEFAULT_STARTING_SCORE)
                
                new_score = current_score + clicks_per_tap
                
                # Обновляем документ в транзакции
                transaction.update(player_ref, {
                    'score': new_score,
                    'last_login': time.time()
                })
                current_score = new_score
            
            return current_score, clicks_per_tap

        new_score, clicks_per_tap = db.run_transaction(lambda t: update_in_transaction(t, player_ref))

        return TapResponse(new_score=new_score, clicks_per_tap=clicks_per_tap)

    except Exception as e:
        logging.error(f"Ошибка обработки клика для пользователя {user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обработать клик.")


@app.post("/upgrade/{user_id}")
async def buy_upgrade(user_id: str):
    """Обрабатывает покупку простого улучшения (увеличение Clicks Per Tap)."""
    player_ref = get_player_doc_ref(user_id)
    if player_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    UPGRADE_COST = 100 
    
    try:
        @firestore.transactional
        def upgrade_transaction(transaction, player_ref):
            snapshot = player_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Игрок не найден.")
            
            state_data = snapshot.to_dict()
            current_score = state_data.get('score', DEFAULT_STARTING_SCORE)
            current_cpt = state_data.get('clicks_per_tap', DEFAULT_CLICKS_PER_TAP)

            if current_score < UPGRADE_COST:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Недостаточно очков для покупки улучшения.")
            
            # Применяем улучшение
            new_score = current_score - UPGRADE_COST
            new_cpt = current_cpt + 1
            
            transaction.update(player_ref, {
                'score': new_score,
                'clicks_per_tap': new_cpt,
                'last_login': time.time()
            })
            return new_score, new_cpt

        new_score, new_cpt = db.run_transaction(lambda t: upgrade_transaction(t, player_ref))
        
        return {"status": "success", "new_score": new_score, "new_clicks_per_tap": new_cpt}

    except HTTPException:
        raise # Пробрасываем HTTP-исключения
    except Exception as e:
        logging.error(f"Ошибка покупки улучшения для пользователя {user_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось купить улучшение.")
