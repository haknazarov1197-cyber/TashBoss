import os
import json
import sys
import logging
import re # Импортируем модуль для регулярных выражений
from firebase_admin import credentials, initialize_app
from fastapi import FastAPI
from contextlib import asynccontextmanager

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# Глобальная переменная для хранения экземпляра Firebase App
firebase_app = None

# --- 1. ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ FIREBASE (Финальная агрессивная очистка) ---

def init_firebase():
    """Инициализирует Firebase Admin SDK, используя переменную окружения."""
    global firebase_app
    
    key_env_var = 'FIREBASE_SERVICE_ACCOUNT_JSON'
    key_start_tag = "-----BEGIN PRIVATE KEY-----"
    
    key_json_str = os.environ.get(key_env_var)
    
    if not key_json_str:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{key_env_var}' не найдена.")
        sys.exit(1)

    try:
        # 1. АГРЕССИВНАЯ ОЧИСТКА ВНЕШНЕЙ JSON-СТРОКИ (Удаляем мусор до '{')
        start_index = key_json_str.find('{')
        if start_index == -1:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начало JSON-объекта (знак '{').")
             sys.exit(1)
        
        key_json_str_cleaned = key_json_str[start_index:].strip()

        # 2. Парсинг JSON
        service_account_info = json.loads(key_json_str_cleaned)

        private_key_str = service_account_info.get('private_key')
        
        if not private_key_str:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Поле 'private_key' отсутствует в JSON.")
             sys.exit(1)

        # 3. ЭКСТРЕМАЛЬНАЯ ОЧИСТКА ПРИВАТНОГО КЛЮЧА
        
        # 3.1. Замена экранированных переводов строк на фактические.
        cleaned_key = private_key_str.strip().replace('\\n', '\n')
        
        # 3.2. ФИНАЛЬНАЯ ФИЛЬТРАЦИЯ: Удаляем ВСЕ непечатные символы, кроме 
        # пробелов, табуляции и явных переводов строк, используя регулярное выражение.
        # Это должно устранить InvalidByte(2, 46).
        # \x20-\x7E - диапазон печатных ASCII символов.
        cleaned_key = re.sub(r'[^\x20-\x7E\n\r\t]+', '', cleaned_key)
        
        # 3.3. Ультра-агрессивная обрезка: ищем тег начала PEM-ключа 
        # и удаляем ВСЕ, что стоит перед ним, на случай, если мусор остался.
        start_key_index = cleaned_key.find(key_start_tag)
        if start_key_index != -1:
            cleaned_key = cleaned_key[start_key_index:]
            logger.info("Private key aggressively truncated to start tag.")
        else:
            logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начальный тег PEM-ключа.")
            sys.exit(1)


        service_account_info['private_key'] = cleaned_key
        
        # 4. DEBUG: Логируем начало ключа для проверки
        log_key_line = cleaned_key.splitlines()[0] if cleaned_key.splitlines() else cleaned_key[:50]
        logger.info(f"Private key cleaning successful. Key start check: {log_key_line[:50]}...")

        # 5. Создание учетных данных и инициализация Firebase
        cred = credentials.Certificate(service_account_info)
        firebase_app = initialize_app(cred)
        logger.info("✅ Firebase успешно инициализирован.")

    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка парсинга JSON: {e}")
        sys.exit(1)
    except Exception as e:
        # Это ловит InvalidData(InvalidByte(2, 46))
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации Firebase: {e}")
        sys.exit(1)

# --- 2. ОПРЕДЕЛЕНИЕ ПРИЛОЖЕНИЯ FASTAPI И ЗАПУСК ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Событие, которое запускается при старте Gunicorn/Uvicorn."""
    init_firebase()
    yield

app = FastAPI(title="Tashboss Backend API", lifespan=lifespan) 

# --- 3. ТЕСТОВЫЙ МАРШРУТ ---

@app.get("/")
def read_root():
    """Простая проверка, что бэкенд запущен и работает."""
    return {"status": "ok", "message": "Backend is running and Firebase should be initialized."}
