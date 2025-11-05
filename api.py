import os
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
# from fastapi.staticfiles import StaticFiles # StaticFiles не используется, т.к. мы обслуживаем файлы вручную

# --- ИСПРАВЛЕНИЕ: Импорт теперь корректен, т.к. bot.py определен ---
from bot import get_telegram_application 
# --- КОНЕЦ ИСПРАВЛЕНИЯ ---

# Импорт Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, auth, exceptions
from telegram import Update

# --- КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ ---

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение переменных окружения
# ПРЕДУПРЕЖДЕНИЕ: В боевой среде Render используйте переменные окружения напрямую,
# без dotenv. Dotenv используется для локального тестирования.
FIREBASE_KEY_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
APP_ID = os.getenv("APP_ID", "default_app_id")

# Константы игры
BASE_COSTS = {"sector1": 100.0, "sector2": 500.0, "sector3": 2500.0}
BASE_RATES = {"sector1": 0.5, "sector2": 2.0, "sector3": 10.0}
COST_MULTIPLIER = 1.15
INITIAL_BALANCE = 100.0

# Инициализация Firebase
db = None
if FIREBASE_KEY_JSON:
    try:
        cred_dict = json.loads(FIREBASE_KEY_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("✅ Firebase Admin SDK и Firestore клиент инициализированы.")
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка при инициализации Firebase: {e}")
        # Выход из приложения, если Firebase не инициализирован
        # exit(1) # В случае FastAPI лучше не выходить, а возвращать 500
else:
    logger.critical("❌ Критическая ошибка: Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY не установлена.")


# Инициализация FastAPI
app = FastAPI(title="TashBoss Game API")

# Настройка CORS middleware (КРИТИЧЕСКИ ВАЖНО для WebApp)
# Разрешаем ВСЕ источники, методы и заголовки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация Telegram Application
tg_app = get_telegram_application()


# --- АУТЕНТИФИКАЦИЯ И УТИЛИТЫ ---

def get_user_doc_ref(user_id: str):
    """Возвращает ссылку на документ пользователя в Firestore."""
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db.collection(f"artifacts/{APP_ID}/users/{user_id}/tashboss_clicker").document(user_id)

async def get_auth_data(request: Request) -> str:
    """Извлекает и верифицирует токен Firebase ID, возвращая UID."""
    if not firebase_admin._apps:
        raise HTTPException(status_code=500, detail="Firebase Admin not initialized")
        
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("❌ Ошибка: Заголовок Authorization отсутствует или некорректен.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or invalid (expected: Bearer <token>)"
        )

    token = auth_header.split("Bearer ")[1]
    
    try:
        # Верификация токена, который должен быть Firebase ID Token 
        decoded_token = auth.verify_id_token(token)
        user_id = decoded_token['uid']
        return user_id
    except exceptions.FirebaseError as e:
        logger.error(f"❌ Ошибка верификации Firebase ID токена: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token"
        )
    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка аутентификации: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed due to an unexpected error"
        )


def calculate_income(data: Dict[str, Any]) -> float:
    """Рассчитывает пассивный доход с момента last_collection_time."""
    
    # Используем Decimal для точных финансовых расчетов
    balance = Decimal(data.get('balance', INITIAL_BALANCE))
    sectors = data.get('sectors', {})
    
    try:
        # Парсинг времени. Firestore/JSON часто хранит его в ISO-формате.
        last_time_str = data.get('last_collection_time')
        if not last_time_str:
             # Если время не указано, считаем от текущего, доход = 0
             last_time = datetime.now(timezone.utc)
        else:
             # Обработка ISO формата с учетом 'Z'
             last_time = datetime.fromisoformat(last_time_str.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        last_time = datetime.now(timezone.utc)
        logger.warning("Некорректный формат last_collection_time. Использовано текущее время.")

    now = datetime.now(timezone.utc)
    # Время, прошедшее в секундах
    time_delta_seconds = max(0, (now - last_time).total_seconds())

    total_income_rate = Decimal(0)
    for sector_id, level in sectors.items():
        rate = Decimal(BASE_RATES.get(sector_id, 0))
        total_income_rate += rate * Decimal(level)

    # Рассчитанный доход
    collected_income = total_income_rate * Decimal(time_delta_seconds)
    
    return float(collected_income)


def calculate_cost(sector_id: str, level: int) -> int:
    """Рассчитывает стоимость следующего уровня сектора."""
    base_cost = BASE_COSTS.get(sector_id, 100.0)
    # Используем Decimal для точных расчетов
    cost = Decimal(base_cost) * (Decimal(COST_MULTIPLIER) ** Decimal(level))
    return int(round(cost)) # Возвращаем целое число


# --- ЭНДПОИНТЫ API И ЛОГИКА ИГРЫ ---

@app.post("/api/load_state")
async def load_state_handler(request: Request):
    """Загружает или инициализирует состояние игры (транзакция)."""
    user_id = await get_auth_data(request)
    doc_ref = get_user_doc_ref(user_id)

    @firestore.transactional
    def transactional_load(transaction, doc_ref):
        """Внутренняя логика транзакции для загрузки/инициализации."""
        try:
            doc = doc_ref.get(transaction=transaction)
            
            if doc.exists:
                data = doc.to_dict()
                # Убедимся, что все ключи присутствуют, даже если они были добавлены позже
                data.setdefault('balance', INITIAL_BALANCE)
                data.setdefault('sectors', {"sector1": 0, "sector2": 0, "sector3": 0})
                data.setdefault('last_collection_time', datetime.now(timezone.utc).isoformat())
                logger.info(f"💾 Состояние для пользователя {user_id} загружено.")
                return data
            else:
                # Инициализация нового состояния
                initial_state = {
                    'balance': INITIAL_BALANCE,
                    'sectors': {"sector1": 0, "sector2": 0, "sector3": 0},
                    'last_collection_time': datetime.now(timezone.utc).isoformat()
                }
                transaction.set(doc_ref, initial_state)
                logger.info(f"✨ Новое состояние для пользователя {user_id} инициализировано.")
                return initial_state
        except Exception as e:
            logger.error(f"Ошибка транзакции load_state: {e}")
            raise HTTPException(status_code=500, detail="Database Transaction Failed")
    
    try:
        data = db.transaction(transactional_load, doc_ref)
        return {"status": "ok", "state": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Критическая ошибка load_state: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "detail": "Internal Server Error"})


@app.post("/api/collect_income")
async def collect_income_handler(request: Request):
    """Рассчитывает и собирает пассивный доход (транзакция)."""
    user_id = await get_auth_data(request)
    doc_ref = get_user_doc_ref(user_id)

    @firestore.transactional
    def transactional_collect(transaction, doc_ref):
        """Внутренняя логика транзакции для сбора дохода."""
        doc = doc_ref.get(transaction=transaction)
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User state not found")

        data = doc.to_dict()
        collected_income = calculate_income(data)
        
        # Обновляем состояние
        new_balance = Decimal(data['balance']) + Decimal(collected_income)
        new_time = datetime.now(timezone.utc).isoformat()
        
        data['balance'] = float(new_balance) # Сохраняем как float
        data['last_collection_time'] = new_time
        
        transaction.set(doc_ref, data)
        logger.info(f"💰 Доход {collected_income:.2f} собран пользователем {user_id}.")
        return data, float(collected_income)

    try:
        data, collected_amount = db.transaction(transactional_collect, doc_ref)
        return {"status": "ok", "state": data, "collected": collected_amount}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Критическая ошибка collect_income: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "detail": "Internal Server Error"})


@app.post("/api/buy_sector")
async def buy_sector_handler(request: Request):
    """Обрабатывает покупку сектора (транзакция)."""
    user_id = await get_auth_data(request)
    doc_ref = get_user_doc_ref(user_id)
    
    try:
        body = await request.json()
        sector_id = body.get("sector")
        if sector_id not in BASE_COSTS:
            raise HTTPException(status_code=400, detail="Invalid sector ID")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    @firestore.transactional
    def transactional_buy(transaction, doc_ref):
        """Внутренняя логика транзакции для покупки сектора."""
        doc = doc_ref.get(transaction=transaction)
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User state not found")
        
        data = doc.to_dict()
        
        # 1. Сбор дохода перед покупкой (актуализация баланса)
        collected_income = calculate_income(data)
        current_balance = Decimal(data.get('balance', 0)) + Decimal(collected_income)
        
        # 2. Определение текущего уровня и стоимости
        current_level = data['sectors'].get(sector_id, 0)
        cost = Decimal(calculate_cost(sector_id, current_level))

        if current_balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient funds")

        # 3. Обновление состояния
        new_balance = current_balance - cost
        data['balance'] = float(new_balance)
        data['sectors'][sector_id] = current_level + 1
        data['last_collection_time'] = datetime.now(timezone.utc).isoformat() # Обновляем время сбора
        
        transaction.set(doc_ref, data)
        logger.info(f"✅ Покупка сектора {sector_id} (Ур. {current_level + 1}) пользователем {user_id} завершена.")
        return data

    try:
        data = db.transaction(transactional_buy, doc_ref)
        return {"status": "ok", "state": data}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Критическая ошибка buy_sector: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "detail": "Internal Server Error"})


# --- TELEGRAM WEBHOOK (ДЛЯ РАБОТЫ /start) ---

if tg_app:
    # Важно: URL-путь должен соответствовать тому, который установлен в setWebhook
    @app.post("/webhook")
    async def telegram_webhook(request: Request):
        """Обрабатывает входящие обновления от Telegram (Webhook)."""
        if not tg_app:
            logger.error("Telegram Application не инициализирован, Webhook не работает.")
            return JSONResponse(content={"message": "Bot not configured"}, status_code=503)

        body = await request.json()
        try:
            # Создаем объект Update из JSON-тела
            update = Update.de_json(body, tg_app.bot)
            
            # Обрабатываем обновление асинхронно
            await tg_app.process_update(update)
            
            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Ошибка обработки Webhook: {e}")
            # Возвращаем 200, даже если ошибка, чтобы Telegram не пытался повторно отправить обновление
            return JSONResponse(content={"status": "error", "detail": str(e)}, status_code=200) 
else:
    logger.warning("Telegram Application не инициализирован. Webhook /start не будет работать.")


# --- СЕРВИНГ СТАТИЧЕСКИХ ФАЙЛОВ ---

# Обработка статических файлов (index.html и app.js)

@app.get("/", response_class=HTMLResponse)
@app.get("/webapp", response_class=HTMLResponse)
async def serve_index():
    """Отдает index.html для корня и WebApp."""
    try:
        # Чтение index.html из файловой системы
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        # Если файл не найден, это критическая ошибка
        raise HTTPException(status_code=404, detail="index.html not found")

@app.get("/app.js")
async def serve_js():
    """Отдает app.js."""
    try:
        with open("app.js", "r", encoding="utf-8") as f:
            js_content = f.read()
        return HTMLResponse(content=js_content, media_type="application/javascript")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="app.js not found")
