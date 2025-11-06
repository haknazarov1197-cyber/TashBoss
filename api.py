import os
import json
import sys
import logging
from fastapi import FastAPI
from firebase_admin import credentials, initialize_app

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# Глобальная переменная для хранения экземпляра Firebase App
firebase_app = None

# --- 1. ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ FIREBASE (Ультра-агрессивная очистка) ---

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
        # 1. АГРЕССИВНАЯ ОЧИСТКА ВНЕШНЕЙ JSON-СТРОКИ
        # Удаляем ВСЕ непечатные символы (включая BOM), чтобы избежать InvalidByte(2, 46) 
        # на уровне парсинга JSON или на уровне ключа.
        
        # Сначала используем strip() для удаления пробелов, затем кодируем/декодируем
        # для фильтрации непечатаемых управляющих символов (контрольных символов).
        # Однако, самый надежный способ – это найти начало JSON ({) и обрезать все перед ним.
        
        # Находим первое вхождение '{' и обрезаем строку.
        start_index = key_json_str.find('{')
        if start_index == -1:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начало JSON-объекта (знак '{').")
             sys.exit(1)
        
        key_json_str_cleaned = key_json_str[start_index:].strip()

        # 2. Парсинг JSON
        service_account_info = json.loads(key_json_str_cleaned)

        private_key = service_account_info.get('private_key')
        
        if not private_key:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Поле 'private_key' отсутствует в JSON.")
             sys.exit(1)

        # 3. ДВОЙНАЯ ОЧИСТКА ПРИВАТНОГО КЛЮЧА
        
        # 3.1. Замена экранированных переводов строк на фактические.
        cleaned_key = private_key.strip().replace('\\n', '\n')
        
        # 3.2. Ультра-агрессивная обрезка: ищем тег начала PEM-ключа 
        # и удаляем ВСЕ, что стоит перед ним.
        
        if not cleaned_key.startswith(key_start_tag):
            start_key_index = cleaned_key.find(key_start_tag)
            
            if start_key_index != -1:
                # Обрезаем все до начала ключа
                cleaned_key = cleaned_key[start_key_index:]
                logger.warning("⚠️ Внимание: Пришлось агрессивно обрезать ключ для удаления мусора перед тегом BEGIN.")
            else:
                logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начальный тег PEM-ключа, даже после очистки.")
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
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка парсинга JSON (проверьте, что в переменной только чистый JSON): {e}")
        sys.exit(1)
    except ValueError as e:
        # Это та самая ошибка InvalidData(InvalidByte(2, 46))
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
