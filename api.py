import os
import json
import sys
import logging
from fastapi import FastAPI, APIRouter
from firebase_admin import credentials, initialize_app

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# --- 1. ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ FIREBASE (Обновленная) ---

def init_firebase():
    """Инициализирует Firebase Admin SDK, используя переменную окружения."""
    
    key_env_var = 'FIREBASE_SERVICE_ACCOUNT_JSON'
    
    if key_env_var not in os.environ:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{key_env_var}' не найдена.")
        sys.exit(1)

    try:
        # 1. Считывание всей строки JSON. .strip() удалит лишние пробелы в начале/конце.
        key_json_str = os.environ[key_env_var].strip()
        
        # 2. Парсинг JSON
        service_account_info = json.loads(key_json_str)

        private_key = service_account_info.get('private_key')
        
        if not private_key:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Поле 'private_key' отсутствует в JSON.")
             sys.exit(1)

        # 3. Двойная очистка PEM-ключа:
        cleaned_key = private_key.strip().replace('\\n', '\n')
        service_account_info['private_key'] = cleaned_key
        
        # 4. DEBUG: Логируем первые 50 символов очищенного ключа
        logger.info(f"Private key cleaning successful. Check start: {cleaned_key[:50].splitlines()[0]}...")

        # 5. Создание учетных данных и инициализация Firebase
        cred = credentials.Certificate(service_account_info)
        initialize_app(cred)
        logger.info("✅ Firebase успешно инициализирован.")

    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка парсинга JSON: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации Firebase: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Неизвестная ошибка при инициализации Firebase: {e}")
        sys.exit(1)

# --- 2. ОПРЕДЕЛЕНИЕ ПРИЛОЖЕНИЯ FASTAPI И ЗАПУСК (ИСПРАВЛЕНИЕ ОШИБКИ 'app') ---

# Gunicorn ищет именно эту переменную 'app'
app = FastAPI(title="Tashboss Backend API") 

@app.on_event("startup")
async def startup_event():
    """Событие, которое запускается при старте Gunicorn/Uvicorn."""
    logger.info("Starting up application and initializing Firebase...")
    init_firebase()
    
# --- 3. ТЕСТОВЫЙ МАРШРУТ ---

@app.get("/")
def read_root():
    """Простая проверка, что бэкенд запущен и работает."""
    return {"status": "ok", "message": "Backend is running and Firebase should be initialized."}
