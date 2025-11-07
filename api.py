import os
import json
import logging
from typing import List
from uuid import uuid4 # Добавляем для генерации UUID

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from firebase_admin import initialize_app, credentials, firestore

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Глобальная переменная для базы данных Firestore
db = None

# --- Модели Pydantic ---

class TaskCreate(BaseModel):
    """Схема для создания новой задачи (ID генерируется автоматически)."""
    title: str = Field(..., description="Название задачи")
    description: str = Field(None, description="Полное описание задачи")
    completed: bool = Field(False, description="Статус выполнения задачи")

class Task(TaskCreate):
    """Полная схема задачи, включая ID."""
    id: str = Field(..., description="Уникальный идентификатор задачи")

    class Config:
        schema_extra = {
            "example": {
                "id": str(uuid4()),
                "title": "Купить продукты",
                "description": "Молоко, яйца, хлеб",
                "completed": False
            }
        }

# --- Инициализация Firebase ---

def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя переменную окружения.
    """
    global db
    logging.info("Запуск инициализации Firebase...")

    try:
        # 1. Загрузка учетных данных. Используем имя, которое вы предпочитаете.
        # ВАЖНО: Убедитесь, что это имя установлено на Render!
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if not service_account_json:
            logging.critical("Переменная окружения FIREBASE_SERVICE_ACCOUNT_JSON не установлена.")
            raise ValueError("Отсутствуют учетные данные Firebase Service Account.")

        # 2. Парсинг JSON
        try:
            # Парсинг строки JSON
            config = json.loads(service_account_json)
        except json.JSONDecodeError as e:
            logging.critical(f"Ошибка парсинга JSON учетной записи сервиса: {e}")
            raise ValueError("Неверный формат JSON для учетных данных Firebase.")

        # 3. КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Исправляем экранированные символы новой строки
        if "private_key" in config:
            config["private_key"] = config["private_key"].replace('\\n', '\n')
            logging.info("Успешно исправлены экранированные символы новой строки в 'private_key'.")
        
        # 4. Инициализация приложения
        cred = credentials.Certificate(config)
        initialize_app(cred)

        # 5. Получение экземпляра Firestore
        db = firestore.client()
        logging.info("✅ Инициализация Firebase успешно завершена.")
        logging.info("Экземпляр Firestore доступен.")

    except Exception as e:
        logging.critical(f"❌ Ошибка при инициализации Firebase: {e}")
        # В продакшене (Render) это вызовет сбой приложения, что правильно.
        raise

# --- Инициализация FastAPI и Lifespan Events ---

app = FastAPI(
    title="Менеджер Задач с Firestore (FastAPI)",
    description="Простой REST API для управления задачами.",
    version="1.0.0"
)

# Используем событие lifespan для инициализации базы данных
@app.on_event("startup")
async def startup_event():
    logging.info("Начало этапа lifespan: Запуск функции init_firebase...")
    init_firebase()

# --- Маршруты API ---

@app.get("/")
def read_root():
    """Простой маршрут для проверки работоспособности API."""
    return {"message": "API Менеджера Задач успешно запущен и работает!"}

@app.post("/tasks/", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(task: TaskCreate):
    """Создает новую задачу."""
    if db is None:
        raise HTTPException(status_code=500, detail="База данных не инициализирована.")

    try:
        # Генерируем уникальный ID для нового документа
        task_id = str(uuid4())
        
        # Подготовка данных для сохранения
        task_data = task.model_dump()
        
        # Сохранение в Firestore (используем сгенерированный ID в качестве имени документа)
        doc_ref = db.collection("tasks").document(task_id)
        await doc_ref.set(task_data) # set() в Firestore - это асинхронная операция

        # Возвращаем полную модель задачи
        return Task(id=task_id, **task_data)
    
    except Exception as e:
        logging.error(f"Ошибка при создании задачи: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")

@app.get("/tasks/", response_model=List[Task])
async def read_tasks():
    """Получает список всех задач."""
    if db is None:
        raise HTTPException(status_code=500, detail="База данных не инициализирована.")
    
    try:
        tasks = []
        # Получение всех документов из коллекции
        docs = db.collection("tasks").stream()
        
        for doc in docs:
            doc_data = doc.to_dict()
            tasks.append(Task(id=doc.id, **doc_data))
        
        return tasks
    
    except Exception as e:
        logging.error(f"Ошибка при чтении задач: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")

@app.get("/tasks/{task_id}", response_model=Task)
async def read_task(task_id: str):
    """Получает одну задачу по ее ID."""
    if db is None:
        raise HTTPException(status_code=500, detail="База данных не инициализирована.")
    
    try:
        doc_ref = db.collection("tasks").document(task_id)
        doc = await doc_ref.get()

        if not doc.exists:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        return Task(id=doc.id, **doc.to_dict())

    except HTTPException:
        raise # Повторно вызываем 404
    except Exception as e:
        logging.error(f"Ошибка при получении задачи {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")

@app.put("/tasks/{task_id}", response_model=Task)
async def update_task(task_id: str, task: TaskCreate):
    """Обновляет существующую задачу по ее ID."""
    if db is None:
        raise HTTPException(status_code=500, detail="База данных не инициализирована.")

    try:
        doc_ref = db.collection("tasks").document(task_id)
        
        # Обновление данных
        task_data = task.model_dump(exclude_unset=True) # exclude_unset=True исключает поля, не переданные в запросе
        await doc_ref.update(task_data)

        # Получаем обновленный документ для возврата
        updated_doc = await doc_ref.get()
        if not updated_doc.exists:
            # Эта ветка должна быть недостижима, если update прошел успешно, но для безопасности
            raise HTTPException(status_code=404, detail="Задача не найдена после обновления")
            
        return Task(id=updated_doc.id, **updated_doc.to_dict())

    except NotFound: # Firestore выбрасывает NotFound, если документа нет
        raise HTTPException(status_code=404, detail="Задача не найдена")
    except Exception as e:
        logging.error(f"Ошибка при обновлении задачи {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")

@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str):
    """Удаляет задачу по ее ID."""
    if db is None:
        raise HTTPException(status_code=500, detail="База данных не инициализирована.")
    
    try:
        doc_ref = db.collection("tasks").document(task_id)
        await doc_ref.delete()
        # В Firestore delete() не выбрасывает исключение, если документа нет,
        # поэтому мы просто возвращаем 204 No Content.
        return {}
    
    except Exception as e:
        logging.error(f"Ошибка при удалении задачи {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {e}")

# Дополнительный импорт для обработки ошибок Firestore
from google.cloud.firestore.exceptions import NotFound # Добавлен импорт для более чистой обработки ошибок
