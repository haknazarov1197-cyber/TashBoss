import os
import json
import sys
import logging
from firebase_admin import credentials, initialize_app

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

def init_firebase():
    """Инициализирует Firebase Admin SDK, используя переменную окружения."""
    
    # Имя переменной окружения, содержащей JSON сервисного аккаунта
    key_env_var = 'FIREBASE_SERVICE_ACCOUNT_JSON'
    
    if key_env_var not in os.environ:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{key_env_var}' не найдена.")
        sys.exit(1)

    try:
        # 1. Считывание всей строки JSON. ОЧЕНЬ ВАЖНО: .strip() удалит любые лишние пробелы/переводы
        # строки, которые могли быть добавлены в Render до символа '{' или после '}'.
        key_json_str = os.environ[key_env_var].strip()
        
        # 2. Парсинг JSON
        service_account_info = json.loads(key_json_str)

        # 3. Усиленная очистка приватного ключа
        private_key = service_account_info.get('private_key')
        
        if not private_key:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Поле 'private_key' отсутствует в JSON.")
             sys.exit(1)

        # 4. Двойная очистка PEM-ключа:
        #    - .strip() удаляет пробелы в начале/конце ключа.
        #    - .replace('\\n', '\n') исправляет двойное экранирование.
        cleaned_key = private_key.strip().replace('\\n', '\n')
        service_account_info['private_key'] = cleaned_key
        
        # 5. DEBUG: Логируем первые 50 символов очищенного ключа
        logger.info(f"Private key cleaning successful. Check start: {cleaned_key[:50]}...")

        # 6. Создание учетных данных и инициализация Firebase
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

# Остальной код вашего api.py должен следовать здесь...
# app = FastAPI() или app = Starlette() и т.д.
# @app.on_event("startup")
# async def startup_event():
#     init_firebase()
