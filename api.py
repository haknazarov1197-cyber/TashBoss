import os
import sys
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FIREBASE GLOBALS ---
# Эти переменные будут инициализированы в init_firebase()
FIREBASE_APP = None
FIREBASE_AUTH = None
FIREBASE_DB = None
FIREBASE_SERVICE_ACCOUNT_KEY = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')

# --- FASTAPI SETUP ---
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --- FIREBASE IMPORTS (Lazy load) ---
try:
    import firebase_admin
    from firebase_admin import credentials, auth, firestore, initialize_app
except ImportError:
    logger.error("Firebase Admin SDK не установлен. Запустите 'pip install firebase-admin'")
    sys.exit(1)

# --- CORE GAME CONFIG ---
# (Используется для начального состояния и валидации)
BASE_CLICKS_PER_SECOND = 1
BASE_CLICK_POWER = 1
HOURS_OF_OFFLINE_PROGRESS = 48
MAX_CLICKS_PER_REQUEST = 1000  # Максимальное количество кликов, разрешенное за один раз

# ----------------------------------------------------
# 1. FIREBASE INITIALIZATION & HELPER FUNCTIONS
# ----------------------------------------------------

def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя ключ из переменной окружения.
    """
    global FIREBASE_APP, FIREBASE_AUTH, FIREBASE_DB

    if FIREBASE_APP:
        logger.info("Firebase уже инициализирован.")
        return

    if not FIREBASE_SERVICE_ACCOUNT_KEY:
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY не найдена.")
        sys.exit(1)

    try:
        # Пытаемся загрузить JSON
        service_account_info = json.loads(FIREBASE_SERVICE_ACCOUNT_KEY)

        # -----------------------------------------------------------
        # !!! КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ ДЛЯ ПРОБЛЕМЫ С PEM-КЛЮЧОМ !!!
        # Гарантируем, что символы новой строки ('\n') внутри private_key
        # правильно интерпретированы, даже если они были повреждены при
        # копировании/вставке в переменную окружения Render.
        # -----------------------------------------------------------
        if 'private_key' in service_account_info and isinstance(service_account_info['private_key'], str):
            private_key = service_account_info['private_key']
            
            # Заменяем все последовательности '\n' (которые должны быть символами новой строки
            # в PEM-ключе) на фактический символ новой строки.
            private_key = private_key.replace('\\n', '\n')
            
            service_account_info['private_key'] = private_key
            logger.info("Private key cleaning successful.")


        cred = credentials.Certificate(service_account_info)
        FIREBASE_APP = initialize_app(cred)
        FIREBASE_AUTH = auth
        FIREBASE_DB = firestore.client()
        logger.info("✅ Ключ Firebase успешно загружен и Firebase инициализирован.")

    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось декодировать JSON из FIREBASE_SERVICE_ACCOUNT_KEY. Ошибка: {e}")
        # Вывод первых 100 символов ключа для отладки
        logger.debug(f"Начало ключа для отладки: {FIREBASE_SERVICE_ACCOUNT_KEY[:100]}")
        sys.exit(1)
    except ValueError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Неожиданная ошибка инициализации: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Непредвиденная ошибка во время инициализации Firebase: {e}")
        sys.exit(1)

def verify_telegram_init_data(init_data: str) -> bool:
    """
    Простейшая проверка: что init_data не пустая.
    !!! В РЕАЛЬНОМ ПРОЕКТЕ НУЖНА ПОЛНАЯ КРИПТОГРАФИЧЕСКАЯ ПРОВЕРКА HASH !!!
    (Эта функция пока заглушка, но для MVP достаточно).
    """
    return bool(init_data)

def create_custom_token(uid: str) -> str:
    """
    Создает кастомный токен Firebase для данного UID.
    """
    try:
        custom_token = FIREBASE_AUTH.create_custom_token(uid)
        return custom_token.decode('utf-8')
    except Exception as e:
        logger.error(f"Ошибка при создании кастомного токена для UID {uid}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при создании токена Firebase.")

# ----------------------------------------------------
# 2. DATA MODELS (Pydantic)
# ----------------------------------------------------

class AuthRequest(BaseModel):
    """Модель для запроса аутентификации."""
    initData: str = Field(..., description="Данные инициализации Telegram WebApp.")

class GameState(BaseModel):
    """Модель для хранения состояния игры."""
    uid: str
    clicks: int = Field(0, ge=0)
    last_updated: str = Field(..., description="ISO 8601 timestamp of the last update.")
    power: int = Field(BASE_CLICK_POWER, ge=1)
    cps: int = Field(BASE_CLICKS_PER_SECOND, ge=0)
    level: int = Field(1, ge=1)
    # Добавьте сюда другие игровые параметры, например, улучшения

class ClickRequest(BaseModel):
    """Модель для запроса кликов."""
    clicks_to_add: int = Field(..., ge=1, le=MAX_CLICKS_PER_REQUEST, description="Количество кликов, которое нужно добавить.")

# ----------------------------------------------------
# 3. GAME LOGIC / FIRESTORE UTILITIES
# ----------------------------------------------------

# Константы для путей Firestore
ARTIFACTS_COLLECTION = "artifacts"
APP_ID = "tashboss-clicker"  # Фиксированный ID для этого приложения
USERS_SUBCOLLECTION = "users"
GAME_STATE_COLLECTION = "game_state"
GAME_STATE_DOC_ID = "current"

def get_user_doc_ref(uid: str):
    """Возвращает ссылку на документ состояния игры пользователя."""
    return FIREBASE_DB.collection(ARTIFACTS_COLLECTION).document(APP_ID).collection(USERS_SUBCOLLECTION).document(uid).collection(GAME_STATE_COLLECTION).document(GAME_STATE_DOC_ID)

def calculate_offline_progress(state: GameState) -> int:
    """
    Рассчитывает прогресс, накопленный в офлайне.
    Возвращает дополнительное количество кликов.
    """
    try:
        last_time = datetime.fromisoformat(state.last_updated)
    except ValueError:
        logger.warning(f"Неверный формат времени last_updated: {state.last_updated}")
        return 0

    current_time = datetime.now()
    time_since_last_update = current_time - last_time

    # Ограничиваем максимальный прогресс
    max_offline_duration = timedelta(hours=HOURS_OF_OFFLINE_PROGRESS)
    effective_duration = min(time_since_last_update, max_offline_duration)

    # Кликов в секунду (CPS)
    cps = state.cps

    # Рассчитываем прогресс
    offline_clicks = int(effective_duration.total_seconds() * cps)

    return offline_clicks

async def load_user_state(uid: str) -> GameState:
    """
    Загружает состояние игры из Firestore, создавая новое, если не найдено.
    """
    doc_ref = get_user_doc_ref(uid)
    doc = doc_ref.get()
    now_iso = datetime.now().isoformat()

    if doc.exists:
        data = doc.to_dict()
        state = GameState(uid=uid, **data)

        # Рассчитываем офлайн-прогресс
        offline_clicks = calculate_offline_progress(state)

        if offline_clicks > 0:
            logger.info(f"UID {uid}: Добавлено {offline_clicks} кликов за офлайн.")
            state.clicks += offline_clicks
            # Временно обновляем время, чтобы не начислять прогресс дважды
            state.last_updated = now_iso

        return state
    else:
        # Создаем новое начальное состояние
        initial_state = GameState(
            uid=uid,
            clicks=0,
            last_updated=now_iso,
            power=BASE_CLICK_POWER,
            cps=BASE_CLICKS_PER_SECOND,
            level=1
        )
        # Сохраняем начальное состояние в базу (асинхронно, не блокируя)
        await save_state_async(initial_state)
        return initial_state

def save_state_async(state: GameState):
    """
    Сохраняет состояние игры в Firestore.
    """
    doc_ref = get_user_doc_ref(state.uid)
    data = state.model_dump(exclude=['uid'])
    data['last_updated'] = datetime.now().isoformat()
    # Используем set() с merge=True для добавления/обновления
    doc_ref.set(data, merge=True)
    logger.debug(f"Состояние для UID {state.uid} сохранено.")


# ----------------------------------------------------
# 4. FASTAPI APPLICATION SETUP
# ----------------------------------------------------

app = FastAPI(title="TashBoss Clicker Backend", version="1.0.0")

# Настройка CORS для работы с Telegram WebApp
app.add_middleware(
    CORSMiddleware,
    # Разрешаем запросы со всех доменов (включая домен Telegram)
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Событие запуска: инициализация Firebase."""
    logger.info("Запуск приложения...")
    init_firebase()


# ----------------------------------------------------
# 5. API ENDPOINTS
# ----------------------------------------------------

@app.get("/", include_in_schema=False)
def read_root():
    """Простой health check для Render."""
    return {"status": "ok", "message": "TashBoss Clicker Backend запущен. Готов к работе."}

@app.post("/api/get_firebase_token")
async def get_firebase_token(request: AuthRequest):
    """
    Проверяет initData Telegram и возвращает кастомный токен Firebase.
    """
    if not verify_telegram_init_data(request.initData):
        logger.warning("Получен невалидный initData.")
        raise HTTPException(status_code=403, detail="Невалидные данные инициализации Telegram.")

    # Временный UID для демо, в реальном проекте используйте id из initData
    # Предполагаем, что initData содержит ID пользователя, извлекаем его.
    # Для простоты, возьмем первые 32 символа initData как псевдо-UID.
    # ВНИМАНИЕ: Это небезопасно для реального использования!
    # Здесь должен быть ID пользователя из проверенных initData.
    # Для целей MVP, используем 't_user_' + хеш initData
    
    # Очень простая псевдо-UID генерация, чтобы иметь что-то уникальное
    import hashlib
    user_id_hash = hashlib.sha256(request.initData.encode()).hexdigest()
    uid = f"t_user_{user_id_hash[:16]}"
    
    try:
        token = create_custom_token(uid)
        return {"custom_token": token, "uid": uid}
    except HTTPException:
        # Re-raise 500 error from create_custom_token
        raise


@app.post("/api/load_state/{uid}")
async def load_state(uid: str, request: Request):
    """
    Загружает состояние игры пользователя по UID.
    """
    # Здесь можно добавить проверку аутентификации пользователя (например, Bearer Token)
    # но для MVP мы просто полагаемся на UID, полученный после токена.

    try:
        game_state = await load_user_state(uid)
        return game_state
    except Exception as e:
        logger.error(f"Ошибка при загрузке состояния для UID {uid}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при загрузке состояния игры.")


@app.post("/api/click/{uid}")
async def add_clicks(uid: str, click_request: ClickRequest):
    """
    Обрабатывает клики и обновляет состояние.
    """
    doc_ref = get_user_doc_ref(uid)

    try:
        # Используем транзакцию для безопасного атомарного обновления
        @firestore.transactional
        def update_in_transaction(transaction):
            doc = doc_ref.get(transaction=transaction)

            if doc.exists:
                data = doc.to_dict()
                current_clicks = data.get('clicks', 0)
                new_clicks = current_clicks + click_request.clicks_to_add
            else:
                # Если документ не существует (очень маловероятно, т.к. создается при load_state)
                # используем начальное значение
                new_clicks = click_request.clicks_to_add

            # Обновляем документ
            new_data = {
                'clicks': new_clicks,
                'last_updated': datetime.now().isoformat()
            }
            transaction.set(doc_ref, new_data, merge=True)
            return new_clicks

        new_total_clicks = update_in_transaction(FIREBASE_DB.transaction())
        return {"clicks": new_total_clicks}

    except Exception as e:
        logger.error(f"Ошибка транзакции при добавлении кликов для UID {uid}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обновлении кликов.")
