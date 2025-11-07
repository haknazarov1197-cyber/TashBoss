import os
import sys
import json
import logging
import asyncio
import time # Добавлен импорт time для time.time()
from typing import List, Optional, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore, initialize_app
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --- Настройка логирования ---
# Устанавливаем формат и уровень логирования
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Глобальные переменные Firebase ---
db = None

# --- Модели Pydantic для данных ---

class Task(BaseModel):
    """Модель задачи."""
    id: Optional[str] = Field(None, description="Уникальный ID задачи (задается Firestore).")
    title: str = Field(..., min_length=1, max_length=100, description="Заголовок задачи.")
    description: Optional[str] = Field(None, description="Подробное описание задачи.")
    is_done: bool = Field(False, description="Статус выполнения задачи.")
    created_at: Optional[float] = Field(None, description="Временная метка создания (Unix Timestamp).")
    updated_at: Optional[float] = Field(None, description="Временная метка последнего обновления.")

# --- Функции инициализации Firebase ---

def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя переменную окружения.
    Если переменная окружения не установлена, вызывается SystemExit.
    """
    global db
    logging.info("Запуск инициализации Firebase...")

    try:
        # 1. Загрузка учетных данных
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if not service_account_json:
            logging.critical("Переменная окружения FIREBASE_SERVICE_ACCOUNT не установлена.")
            raise ValueError("Отсутствуют учетные данные Firebase Service Account.")

        # 2. Парсинг JSON
        creds_dict = json.loads(service_account_json)
        
        # 3. Создание учетных данных
        cred = credentials.Certificate(creds_dict)
        
        # 4. Инициализация приложения
        try:
            initialize_app(cred)
            logging.info("✅ Инициализация Firebase успешно завершена.")
        except ValueError as e:
            if "already exists" in str(e):
                logging.info("Приложение Firebase уже инициализировано, продолжаем.")
            else:
                raise e
        except Exception as e:
            raise e

        # 5. Получение экземпляра Firestore
        # !!! ИСПРАВЛЕНО: Используем database_id вместо database !!!
        db = firestore.client(database_id="(default)") 
        logging.info("Экземпляр Firestore доступен.")

    except ValueError as e:
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Тип ошибки: ValueError. Детали: {e}")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Тип ошибки: {type(e).__name__}. Детали: {e}")
        sys.exit(1)


# --- Настройка FastAPI ---

app = FastAPI(title="Task Manager API (FastAPI + Firestore)")

# Настройка CORS для разрешения запросов с фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """
    Вызывается при запуске приложения.
    """
    logging.info("Начало этапа lifespan: Запуск функции init_firebase...")
    await asyncio.to_thread(init_firebase)


# --- Пути API (Endpoints) ---

@app.get("/", status_code=status.HTTP_200_OK)
async def health_check():
    """Проверка состояния сервиса."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is starting up, Firebase not yet initialized."
        )
    try:
        # Простой тест доступности Firestore
        db.collection("health_check").document("test").get()
        return {"status": "ok", "message": "API is operational and Firebase connection is successful."}
    except Exception as e:
        logging.error(f"Health check failed due to Firestore connection error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"API is operational but Firestore connection failed: {e}"
        )


@app.get("/tasks", response_model=List[Task], status_code=status.HTTP_200_OK)
async def get_all_tasks():
    """Получает все задачи из коллекции 'tasks'."""
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    tasks_ref = db.collection("tasks")
    try:
        docs = tasks_ref.stream()
        task_list = []
        for doc in docs:
            task_data = doc.to_dict()
            task_data['id'] = doc.id
            task_list.append(Task(**task_data))
        
        # Сортируем задачи по времени создания
        task_list.sort(key=lambda t: t.created_at or 0, reverse=True)
        return task_list
    except Exception as e:
        logging.error(f"Error fetching tasks: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch tasks.")

@app.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(task: Task):
    """Создает новую задачу."""
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    tasks_ref = db.collection("tasks")
    current_time = time.time() # Исправлено на time.time()
    
    # Подготавливаем данные для Firestore
    task_data = task.model_dump(exclude={'id', 'created_at', 'updated_at'})
    task_data['created_at'] = current_time
    task_data['updated_at'] = current_time
    task_data['is_done'] = False
    
    try:
        doc_ref = tasks_ref.add(task_data)
        
        created_task = Task(id=doc_ref.id, **task_data)
        return created_task
    except Exception as e:
        logging.error(f"Error creating task: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create task.")

@app.put("/tasks/{task_id}", response_model=Task, status_code=status.HTTP_200_OK)
async def update_task(task_id: str, task_update: Task):
    """Обновляет существующую задачу по ID."""
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    task_ref = db.collection("tasks").document(task_id)
    
    update_data = task_update.model_dump(exclude_none=True, exclude={'id', 'created_at', 'updated_at'})
    
    if not update_data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No fields provided for update.")

    update_data['updated_at'] = time.time() # Исправлено на time.time()
    
    try:
        task_ref.update(update_data)
        
        updated_doc = task_ref.get()
        if not updated_doc.exists:
            # Note: The firestore.client().update() method will raise a NotFound exception 
            # if the document doesn't exist, which will be caught below. 
            # This manual check is redundant if using client libraries, but safe.
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task not found after update attempt.")

        updated_data = updated_doc.to_dict()
        updated_task = Task(id=updated_doc.id, **updated_data)
        return updated_task
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error updating task {task_id}: {e}")
        # В более сложных случаях здесь можно проверить, является ли ошибка 404 (NotFound),
        # но для простоты оставляем общий 500, так как Update не должен быть вызван для несуществующего документа.
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update task.")


@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str):
    """Удаляет задачу по ID."""
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    task_ref = db.collection("tasks").document(task_id)
    
    try:
        task_ref.delete()
        return {}
    except Exception as e:
        logging.error(f"Error deleting task {task_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete task.")
