import os
import sys
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

# --- Настройка логирования ---
# Используем стандартный модуль logging для вывода сообщений
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Глобальные переменные Firebase ---
# Глобально храним объект базы данных после успешной инициализации
db = None

# --- Модели данных для FastAPI (Pydantic) ---
# Пример модели для данных, которые мы будем сохранять
class Task(BaseModel):
    title: str
    description: str | None = None
    completed: bool = False

# --- Функция инициализации Firebase ---
def init_firebase():
    """
    Инициализирует Firebase Admin SDK.
    Получает учетные данные из переменной окружения FIREBASE_SERVICE_ACCOUNT.
    """
    global db
    logger.info("Запуск инициализации Firebase...")

    # 1. Проверка наличия переменной окружения
    service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    
    if not service_account_json:
        # Выводим критическое сообщение и завершаем работу
        logger.critical("Переменная окружения FIREBASE_SERVICE_ACCOUNT не установлена.")
        try:
            # Импортируем firebase_admin только здесь, чтобы избежать ошибки импорта,
            # если мы знаем, что все равно будем завершать работу
            import firebase_admin 
            logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Детали: Отсутствуют учетные данные Firebase Service Account.")
        except ImportError:
            pass # Если даже firebase_admin не установлен, ошибка все равно критическая
        
        # Если инициализация невозможна, мы не можем безопасно продолжать
        raise ValueError("Отсутствуют учетные данные Firebase Service Account.")

    # 2. Инициализация Admin SDK
    try:
        # Импортируем Admin SDK
        import firebase_admin
        from firebase_admin import credentials, firestore

        # Преобразуем JSON-строку в объект Python
        service_account_info = json.loads(service_account_json)
        
        # Создаем учетные данные
        cred = credentials.Certificate(service_account_info)
        
        # Инициализируем приложение Firebase
        # Проверяем, не было ли приложение уже инициализировано (например, Gunicorn-воркером)
        if not firebase_admin.get_app(name="[DEFAULT]"):
             firebase_admin.initialize_app(cred)
        
        # Получаем ссылку на Firestore DB
        db = firestore.client()
        logger.info("✅ Инициализация Firebase успешно завершена.")

    except (ImportError, ValueError, json.JSONDecodeError) as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Тип ошибки: {type(e).__name__}. Детали: {e}")
        # Завершаем процесс при неудачной инициализации
        sys.exit(1)


# --- Функция для управления жизненным циклом (Lifespan) приложения ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Функция, которая запускается перед стартом сервера и после его остановки.
    Используется для инициализации Firebase.
    """
    logger.info("Начало этапа lifespan: Запуск функции init_firebase...")
    init_firebase() # Запускаем инициализацию перед стартом
    try:
        yield # Сервер обрабатывает запросы
    finally:
        logger.info("Конец этапа lifespan: Завершение работы.")


# --- Создание экземпляра FastAPI ---
app = FastAPI(
    title="Tashboss API", 
    version="1.0.0", 
    lifespan=lifespan
)


# --- Маршруты API ---

# 1. Проверочный маршрут
@app.get("/", summary="Проверка работоспособности")
async def root():
    return {"message": "Tashboss API запущен и работает!"}

# 2. Маршрут для проверки статуса Firebase (потребуется после настройки)
@app.get("/status", summary="Проверка статуса Firebase")
async def get_status():
    if db:
        # В рабочем приложении можно сделать тестовый запрос к DB
        return {"status": "ok", "db_initialized": True, "message": "API и Firebase работают."}
    else:
        return {"status": "error", "db_initialized": False, "message": "API работает, но Firebase не инициализирован."}

# 3. Маршрут для создания задачи (ПРИМЕР)
@app.post("/tasks", summary="Создать новую задачу")
async def create_task(task: Task):
    if not db:
        return {"error": "База данных не инициализирована."}, 500
    
    # ПРИМЕР: сохранение в Firestore
    try:
        doc_ref = await db.collection("tasks").add(task.model_dump())
        return {"id": doc_ref[1].id, "task": task}
    except Exception as e:
        logger.error(f"Ошибка при сохранении задачи: {e}")
        return {"error": f"Ошибка базы данных: {e}"}, 500

# 4. Маршрут для получения всех задач (ПРИМЕР)
@app.get("/tasks", summary="Получить все задачи")
async def get_tasks():
    if not db:
        return {"error": "База данных не инициализирована."}, 500
    
    # ПРИМЕР: чтение из Firestore
    try:
        tasks = []
        docs = db.collection("tasks").stream()
        for doc in docs:
            task_data = doc.to_dict()
            task_data["id"] = doc.id
            tasks.append(task_data)
        return {"tasks": tasks}
    except Exception as e:
        logger.error(f"Ошибка при чтении задач: {e}")
        return {"error": f"Ошибка базы данных: {e}"}, 500
