import os
import sys
import json
import logging

# Импорты для FastAPI
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Импорты для Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials

# --------------------------
# 1. КОНФИГУРАЦИЯ ЛОГГИРОВАНИЯ
# --------------------------
# Настраиваем логгирование для вывода информации о запуске
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# Глобальная переменная для Firebase (инициализируется в startup_event)
FIREBASE_APP = None

# --------------------------
# 2. ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ FIREBASE
# --------------------------

def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя переменную окружения
    'FIREBASE_SERVICE_ACCOUNT_KEY'. Включает очистку строки ключа.
    """
    global FIREBASE_APP
    FIREBASE_KEY_VAR = 'FIREBASE_SERVICE_ACCOUNT_KEY'
    key_str = os.environ.get(FIREBASE_KEY_VAR)

    logger.info("Попытка инициализации Firebase...")

    if not key_str:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{FIREBASE_KEY_VAR}' не найдена. Завершение работы.")
        sys.exit(1)

    # *** КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ для ошибки UnicodeDecodeError (0xb7) ***
    # Очистка строки: удаление всех ведущих и завершающих пробелов/невидимых символов,
    # которые могли быть скопированы вместе с ключом.
    key_str_cleaned = key_str.strip()
    
    if not key_str_cleaned:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{FIREBASE_KEY_VAR}' пуста после очистки. Завершение работы.")
        sys.exit(1)

    try:
        # Прямая загрузка JSON из очищенной строки
        service_account_info = json.loads(key_str_cleaned)
        
    except json.JSONDecodeError as e:
        # Ловим ошибку, если строка не является корректным JSON
        logger.error(f"Ошибка JSON декодирования: {e}")
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Ключ не является корректным JSON после очистки. Завершение работы.")
        sys.exit(1)
    except Exception as e:
        # Ловим другие непредвиденные ошибки
        logger.error(f"Непредвиденная ошибка при чтении ключа Firebase: {e}")
        sys.exit(1)

    # Продолжение инициализации Firebase
    try:
        cred = credentials.Certificate(service_account_info)
        FIREBASE_APP = firebase_admin.initialize_app(cred)
        logger.info("✅ Firebase Admin SDK успешно инициализирован.")
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать Firebase Admin SDK: {e}")
        sys.exit(1)

# --------------------------
# 3. НАСТРОЙКА ПРИЛОЖЕНИЯ FASTAPI
# --------------------------

app = FastAPI(
    title="Tashboss API Service",
    version="1.0.0",
    description="Backend service for Tashboss application."
)

@app.on_event("startup")
async def startup_event():
    """
    Вызывается при запуске приложения (Lifespan Hook).
    """
    logger.info("Запуск функции FastAPI startup_event...")
    init_firebase()

@app.on_event("shutdown")
def shutdown_event():
    """
    Вызывается при завершении работы приложения.
    """
    logger.info("Завершение работы приложения.")

# --------------------------
# 4. ЭНДПОЙНТЫ API
# --------------------------

@app.get("/health", response_class=JSONResponse)
def health_check():
    """
    Проверка работоспособности сервиса.
    Возвращает статус инициализации Firebase.
    """
    status = "OK" if FIREBASE_APP else "FIREBASE_ERROR"
    return {"status": status, "message": "Сервис запущен"}

@app.get("/")
def read_root():
    """
    Основной маршрут.
    """
    return {"Hello": "World", "Service": "Tashboss API"}
