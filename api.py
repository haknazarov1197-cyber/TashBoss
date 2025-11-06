import os
import json
import sys
import logging
from fastapi import FastAPI
from firebase_admin import credentials, initialize_app

# Настройка логирования
# Мы настроили подробное логирование, чтобы видеть, что происходит.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# --- 1. ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ FIREBASE (Ультра-агрессивная очистка) ---

def init_firebase():
    """Инициализирует Firebase Admin SDK, используя переменную окружения."""
    
    key_env_var = 'FIREBASE_SERVICE_ACCOUNT_JSON'
    
    if key_env_var not in os.environ:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{key_env_var}' не найдена.")
        sys.exit(1)

    try:
        # 1. Считывание всей строки JSON и удаление внешних пробелов.
        key_json_str = os.environ[key_env_var].strip()
        
        # 2. Парсинг JSON
        service_account_info = json.loads(key_json_str)

        private_key = service_account_info.get('private_key')
        
        if not private_key:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Поле 'private_key' отсутствует в JSON.")
             sys.exit(1)

        # 3. Двойная очистка PEM-ключа:
        
        # 3.1. Замена экранированных переводов строк и удаление внешних пробелов.
        cleaned_key = private_key.strip().replace('\\n', '\n')
        
        # 3.2. Ультра-агрессивная очистка: ищем начало PEM-ключа 
        # и удаляем ВСЕ, что стоит перед ним. Это должно устранить 
        # непечатные байты, добавляемые хостингом (как InvalidByte(2, 46)).
        key_start_tag = "-----BEGIN PRIVATE KEY-----"
        
        if not cleaned_key.startswith(key_start_tag):
            start_index = cleaned_key.find(key_start_tag)
            
            if start_index != -1:
                # Обрезаем все до начала ключа
                cleaned_key = cleaned_key[start_index:]
                logger.warning("⚠️ Внимание: Пришлось агрессивно обрезать ключ для удаления мусора перед тегом BEGIN.")
            else:
                logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начальный тег PEM-ключа, даже после очистки.")
                sys.exit(1)

        service_account_info['private_key'] = cleaned_key
        
        # 4. DEBUG: Логируем первую строку ключа для проверки
        log_key_line = cleaned_key.splitlines()[0] if cleaned_key.splitlines() else cleaned_key[:50]
        logger.info(f"Private key cleaning successful. Key start check: {log_key_line[:50]}...")

        # 5. Создание учетных данных и инициализация Firebase
        cred = credentials.Certificate(service_account_info)
        initialize_app(cred)
        logger.info("✅ Firebase успешно инициализирован.")

    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка парсинга JSON: {e}")
        sys.exit(1)
    except ValueError as e:
        # Логируем точную ошибку из библиотеки для отладки
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка инициализации Firebase: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Неизвестная ошибка при инициализации Firebase: {e}")
        sys.exit(1)

# --- 2. ОПРЕДЕЛЕНИЕ ПРИЛОЖЕНИЯ FASTAPI И ЗАПУСК ---

app = FastAPI(title="Tashboss Backend API") 

@app.on_event("startup")
def startup_event():
    """Событие, которое запускается при старте Gunicorn/Uvicorn."""
    logger.info("Starting up application and initializing Firebase...")
    # Запускаем инициализацию при старте приложения
    init_firebase()
    
# --- 3. ТЕСТОВЫЙ МАРШРУТ ---

@app.get("/")
def read_root():
    """Простая проверка, что бэкенд запущен и работает."""
    return {"status": "ok", "message": "Backend is running and Firebase should be initialized."}
