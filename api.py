import os
import json
import sys
import firebase_admin
from firebase_admin import credentials
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from typing import Dict, Any

# Глобальная переменная для хранения экземпляра инициализированного приложения Firebase
FIREBASE_APP = None 
# Глобальная переменная для хранения экземпляра базы данных Firestore (если используется)
FIRESTORE_DB = None 

def init_firebase():
    """
    Инициализирует Firebase Admin SDK. 
    Очищает закрытый ключ, исправляя экранирование символов новой строки (\n) 
    из переменной окружения.
    """
    global FIREBASE_APP, FIRESTORE_DB

    # Проверка, инициализировано ли приложение Firebase, чтобы избежать ошибки
    if firebase_admin._apps:
        print("INFO: Приложение Firebase уже инициализировано.", file=sys.stdout)
        return

    try:
        print("INFO: Запуск инициализации Firebase...")
        
        # 1. Получение строки JSON учетной записи службы из переменной окружения
        SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if not SERVICE_ACCOUNT_JSON:
            print("CRITICAL: Переменная окружения FIREBASE_SERVICE_ACCOUNT не установлена.", file=sys.stderr)
            raise ValueError("Отсутствуют учетные данные Firebase Service Account.")

        # 2. Преобразование строки JSON в словарь Python
        # Предполагаем, что строка SERVICE_ACCOUNT_JSON не была дважды закодирована/экранирована
        service_account_info: Dict[str, Any] = json.loads(SERVICE_ACCOUNT_JSON)

        # 3. КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Замена экранированных символов новой строки ('\\n') на настоящие ('\n')
        # Это исправляет ошибку "Unable to load PEM file. InvalidData(InvalidByte(2, 46))".
        if 'private_key' in service_account_info and isinstance(service_account_info['private_key'], str):
            # Проблема возникает, когда '\n' в JSON файле становится '\\n' при помещении в env var.
            # Мы меняем '\\n' обратно на '\n'.
            service_account_info['private_key'] = service_account_info['private_key'].replace('\\n', '\n')
            print("INFO: Приватный ключ успешно очищен (замена \\n на \n).")
            # Для отладки можно добавить проверку: print(service_account_info['private_key'][:50] + '...')

        # 4. Создание объекта учетных данных
        cred = credentials.Certificate(service_account_info)

        # 5. Инициализация приложения Firebase
        FIREBASE_APP = firebase_admin.initialize_app(cred)
        # Если вы используете Firestore, можете инициализировать его здесь:
        # FIRESTORE_DB = firebase_admin.firestore.client() 

        print("✅ Firebase Admin SDK успешно инициализирован.")
        
    except Exception as e:
        # Логирование критической ошибки и выход, как было в исходном трассировке
        error_type = type(e).__name__
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Тип ошибки: {error_type}. Детали: {e}", file=sys.stderr)
        
        # Выход с кодом ошибки 1 для завершения запуска Gunicorn/Uvicorn worker
        sys.exit(1)


# Определение контекстного менеджера для управления жизненным циклом (Lifespan) приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Выполняется при запуске и завершении работы приложения.
    """
    # 1. Этап Startup: Инициализация Firebase
    print("INFO: Начало этапа lifespan: Запуск функции init_firebase...")
    init_firebase()
    
    # После завершения инициализации (или выхода sys.exit(1) в случае ошибки)
    # приложение переходит в рабочее состояние.
    yield
    
    # 2. Этап Shutdown: Очистка ресурсов (при необходимости)
    print("INFO: Этап lifespan завершен. Завершение работы приложения.")
    # Здесь можно добавить логику закрытия соединений или освобождения ресурсов

# Создание экземпляра приложения FastAPI, используя определенный lifespan
app = FastAPI(
    title="Firebase Admin API",
    description="A basic FastAPI service with corrected Firebase Admin SDK initialization.",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/")
def read_root():
    """Базовый маршрут для проверки работоспособности сервиса."""
    if FIREBASE_APP:
        # Можно добавить проверку на реальное подключение к Firestore здесь,
        # но для простоты просто проверяем инициализацию объекта приложения.
        return {"status": "ok", "message": "API запущен и Firebase Admin SDK инициализирован."}
    else:
        # Это состояние, по идее, не должно быть достигнуто, если init_firebase() 
        # завершается с sys.exit(1) при ошибке.
        raise HTTPException(status_code=503, detail="API запущен, но Firebase Admin SDK не инициализирован.")

# Пример дополнительного маршрута (необязательно, но полезно)
@app.get("/status")
def get_firebase_status():
    """Проверка статуса инициализации Firebase."""
    if FIREBASE_APP and firebase_admin._apps:
        return {"status": "ready", "app_name": FIREBASE_APP.name}
    else:
        raise HTTPException(status_code=500, detail="Firebase не инициализирован.")
        
# Пример использования:
# Для запуска: uvicorn api:app --reload
# В продакшене (как в вашем логе): gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
