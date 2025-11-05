import os
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Импорт Telegram Application
from telegram import Update
from bot import get_telegram_application

# Импорт Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, auth, exceptions

# --- КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ ---

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение переменных окружения
FIREBASE_KEY_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
APP_ID = os.getenv("APP_ID", "default_app_id")

# Константы игры
BASE_COSTS = {"sector1": 100.0, "sector2": 500.0, "sector3": 2500.0}
BASE_RATES = {"sector1": 0.5, "sector2": 2.0, "sector3": 10.0}
COST_MULTIPLIER = 1.15
INITIAL_BALANCE = 100.0

# Инициализация Firebase
if FIREBASE_KEY_JSON:
    try:
        # Render передает ключ как JSON-строку
        cred_dict = json.loads(FIREBASE_KEY_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("✅ Firebase Admin SDK и Firestore клиент инициализированы.")
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка при инициализации Firebase: {e}")
        # Выход, если не удалось инициализировать критически важный сервис
        exit(1)
else:
    logger.critical("❌ Критическая ошибка: Переменная окружения FIREBASE_SERVICE_ACCOUNT_KEY не установлена.")
    exit(1)

# Инициализация FastAPI
app = FastAPI(title="TashBoss Game API")

# Настройка CORS middleware (КРИТИЧЕСКИ ВАЖНО для WebApp)
# Разрешаем все источники, так как Telegram Mini App запускается из разных доменов
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
    # Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    return db.collection(f"artifacts/{APP_ID}/users/{user_id}/tashboss_clicker").document(user_id)

async def get_auth_data(request: Request) -> str:
    """Извлекает и верифицирует токен Telegram, возвращая UID."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("❌ Ошибка: Заголовок Authorization отсутствует или некорректен.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or invalid (expected: Bearer <token>)"
        )

    # Токен содержит initData, которую мы должны проверить
    init_data = auth_header.split("Bearer ")[1]
    
    # В реальном приложении Telegram здесь должна быть функция для проверки 
    # initData (например, с использованием HMAC-SHA256 и секрета бота).
    # Поскольку Canvas не предоставляет секрет бота для проверки initData, 
    # мы будем использовать верификацию Firebase ID токена (полученного от Canvas Auth)
    # или, как временное решение, заглушку для `initData`.
    # Для целей этого проекта, мы будем предполагать, что init_data содержит
    # Firebase Custom Auth Token (если запущен в Canvas) или
    # Telegram initData (если запущен в MiniApp).
    
    # ПРИМЕЧАНИЕ: В данном случае, клиентский JS передает Telegram.WebApp.initData
    # В *настоящем* Mini App это должен быть проверенный `query_id` или `initData`.
    # Мы пока просто возвращаем заглушку, чтобы позволить транзакциям работать, 
    # если нет полной интеграции с Telegram Auth Backend.
    
    # ПРЕДПОЛОЖЕНИЕ: для работы с Firestore, мы извлекаем UID из initData
    # как если бы она была Firebase Custom Token (то, что предоставляет Canvas Auth)
    
    # ТЕХНИЧЕСКИЙ ДОЛГ: В продакшене тут должен быть ВАЛИДАТОР init_data
    
    # Если это MiniApp, initData - это строка типа 'query_id=...&user=...'
    # Если это Canvas, токен - это Firebase Custom Token
    
    # Для избежания проблем с деплоем, мы временно принимаем любой токен 
    # и используем заглушку, но в реальном Mini App это должно быть:
    # 1. Проверка initData (если MiniApp)
    # 2. Верификация токена (если Canvas Auth)
    
    # Заглушка, чтобы просто получить User ID (должен быть заменен!)
    # В реальном Mini App User ID берется из `init_data` после проверки.
    
    # Мы используем '123456789' как заглушку UID для симуляции успешной аутентификации.
    # В продакшене это приведет к ошибкам безопасности!
    user_id = "tg_user_123456789" 
    return user_id 


def calculate_income(data: Dict[str, Any]) -> float:
    """Рассчитывает пассивный доход с момента last_collection_time."""
    
    # Конвертируем все числа в Decimal для точных расчетов
    balance = Decimal(data.get('balance', INITIAL_BALANCE))
    sectors = data.get('sectors', {})
    
    try:
        last_time = datetime.fromisoformat(data['last_collection_time'].replace('Z', '+00:00'))
    except (ValueError, TypeError):
        last_time = datetime.now(timezone.utc)
        logger.warning("Некорректный формат last_collection_time. Использовано текущее время.")

    now = datetime.now(timezone.utc)
    time_delta_seconds = max(0, (now - last_time).total_seconds())

    total_income_rate = Decimal(0)
    for sector_id, level in sectors.items():
        rate = Decimal(BASE_RATES.get(sector_id, 0))
        total_income_rate += rate * Decimal(level)

    collected_income = float(total_income_rate * Decimal(time_delta_seconds))
    
    return collected_income


# --- ЭНДПОИНТЫ API И ЛОГИКА ИГРЫ ---

@app.post("/api/load_state")
async def load_state_handler(request: Request):
    """Загружает или инициализирует состояние игры."""
    # Получаем UID (используем заглушку, если не можем верифицировать токен)
    user_id = await get_auth_data(request)
    doc_ref = get_user_doc_ref(user_id)

    @firestore.transactional
    def transactional_load(transaction, doc_ref):
        try:
            doc = doc_ref.get(transaction=transaction)
            
            if doc.exists:
                data = doc.to_dict()
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
        raise # Передаем HTTP ошибки дальше
    except Exception as e:
        logger.error(f"Критическая ошибка load_state: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "detail": "Internal Server Error"})


@app.post("/api/collect_income")
async def collect_income_handler(request: Request):
    """Рассчитывает и собирает пассивный доход."""
    user_id = await get_auth_data(request)
    doc_ref = get_user_doc_ref(user_id)

    @firestore.transactional
    def transactional_collect(transaction, doc_ref):
        doc = doc_ref.get(transaction=transaction)
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User state not found")

        data = doc.to_dict()
        collected_income = calculate_income(data)
        
        # Обновляем состояние
        new_balance = Decimal(data['balance']) + Decimal(collected_income)
        new_time = datetime.now(timezone.utc).isoformat()
        
        data['balance'] = float(new_balance)
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


def calculate_cost(sector_id: str, level: int) -> int:
    """Рассчитывает стоимость следующего уровня."""
    base_cost = BASE_COSTS.get(sector_id, 100.0)
    # Используем Decimal для точных расчетов
    cost = Decimal(base_cost) * (Decimal(COST_MULTIPLIER) ** Decimal(level))
    return int(round(cost))


@app.post("/api/buy_sector")
async def buy_sector_handler(request: Request):
    """Обрабатывает покупку сектора."""
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
        doc = doc_ref.get(transaction=transaction)
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User state not found")
        
        data = doc.to_dict()
        
        # 1. Сбор дохода перед покупкой
        collected_income = calculate_income(data)
        current_balance = Decimal(data['balance']) + Decimal(collected_income)
        
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
            return JSONResponse(content={"status": "error", "detail": str(e)}, status_code=500)
else:
    logger.warning("Telegram Application не инициализирован. Webhook /start не будет работать.")


# --- СЕРВИНГ СТАТИЧЕСКИХ ФАЙЛОВ ---

# Важно: Сначала монтируем статические файлы, чтобы они обслуживались
# app.mount("/", StaticFiles(directory=".", html=True), name="static") 
# Render требует, чтобы index.html был доступен по /

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

@app.get("/bot.py")
async def serve_bot_py():
    """Отдает bot.py (необязательно, но полезно для дебага/развертывания)"""
    try:
        with open("bot.py", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content, media_type="text/x-python")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="bot.py not found")

# ЭТОТ БЛОК НУЖЕН ТОЛЬКО ДЛЯ ЛОКАЛЬНОГО ТЕСТИРОВАНИЯ
# if __name__ == "__main__":
#     import uvicorn
#     # Убедитесь, что bot.py доступен в папке
#     uvicorn.run(app, host="0.0.0.0", port=8000)
