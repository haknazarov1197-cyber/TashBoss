import os
import sys
import json
import logging
import asyncio
import time 
from typing import List, Optional, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore, initialize_app
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --- Настройка логирования ---
# Устанавливаем формат и уровень логирования
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Настройка констант для Firestore ---
# ВАЖНО: Эти значения должны быть получены динамически в реальном приложении.
# Для целей тестирования API используем заглушки.
APP_ID = 'your_app_id_placeholder' # Замените на фактический ID вашего приложения
USER_ID = 'test_user_placeholder'  # Замените на фактический ID пользователя (из токена)
COLLECTION_NAME = 'tasks'

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
    Включает КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ для символов новой строки.
    """
    global db
    logging.info("Запуск инициализации Firebase...")

    try:
        # 1. Загрузка учетных данных. Используем более явное имя переменной.
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if not service_account_json:
            logging.critical("Переменная окружения FIREBASE_SERVICE_ACCOUNT_JSON не установлена.")
            raise ValueError("Отсутствуют учетные данные Firebase Service Account.")

        # 2. Парсинг JSON
        creds_dict = json.loads(service_account_json)
        
        # 3. КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Замена экранированных '\n' на реальные символы новой строки
        private_key = creds_dict.get('private_key')
        if private_key and isinstance(private_key, str):
            # Эта замена необходима, если облачная среда (Render) автоматически 
            # экранирует '\n' в переменной окружения.
            creds_dict['private_key'] = private_key.replace(r'\n', '\n')
            logging.info("Успешно исправлены экранированные символы новой строки в 'private_key'.")

        # 4. Создание учетных данных
        cred = credentials.Certificate(creds_dict)
        
        # 5. Инициализация приложения
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

        # 6. Получение экземпляра Firestore
        db = firestore.client(database_id="(default)") 
        logging.info("Экземпляр Firestore доступен.")

    except ValueError as e:
        # Обрабатывает ошибки отсутствия переменной окружения или неверного JSON-формата
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Тип ошибки: ValueError. Детали: {e}")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Инициализация Firebase не удалась. Тип ошибки: {type(e).__name__}. Детали: {e}")
        sys.exit(1)


def get_collection_ref():
    """Возвращает ссылку на коллекцию 'tasks' в соответствии с правилами Canvas/Firestore."""
    if db is None:
        return None
    
    # Путь: /artifacts/{APP_ID}/users/{USER_ID}/tasks
    return db.collection('artifacts').document(APP_ID).collection('users').document(USER_ID).collection(COLLECTION_NAME)


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
    """Вызывается при запуске приложения."""
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
        # Простой тест доступности Firestore (запрос к несуществующему пути)
        # Это более надежный тест, чем просто вызов doc().get(), так как он инициирует сетевой запрос.
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
    """Получает все задачи из коллекции текущего пользователя."""
    tasks_ref = get_collection_ref()
    if tasks_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    try:
        # NOTE: Если вы хотите сортировать на стороне сервера, вам нужно будет создать индекс в Firestore.
        # Для простоты, пока будем сортировать в коде.
        docs = tasks_ref.stream()
        task_list = []
        for doc in docs:
            task_data = doc.to_dict()
            task_data['id'] = doc.id
            task_list.append(Task(**task_data))
        
        # Сортируем задачи по времени создания (если оно есть)
        task_list.sort(key=lambda t: t.created_at or 0, reverse=True)
        return task_list
    except Exception as e:
        logging.error(f"Error fetching tasks: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch tasks.")

@app.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(task: Task):
    """Создает новую задачу для текущего пользователя."""
    tasks_ref = get_collection_ref()
    if tasks_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    current_time = time.time()
    
    # Подготавливаем данные для Firestore, исключая ID (который генерируется) и метки времени
    task_data = task.model_dump(exclude={'id', 'created_at', 'updated_at'})
    task_data['created_at'] = current_time
    task_data['updated_at'] = current_time
    task_data['is_done'] = False
    
    try:
        # doc_ref здесь является кортежем (update_time, DocumentReference)
        _, doc_ref = tasks_ref.add(task_data)
        
        # Возвращаем созданную задачу с присвоенным ID
        created_task = Task(id=doc_ref.id, **task_data)
        return created_task
    except Exception as e:
        logging.error(f"Error creating task: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create task.")

@app.put("/tasks/{task_id}", response_model=Task, status_code=status.HTTP_200_OK)
async def update_task(task_id: str, task_update: Task):
    """Обновляет существующую задачу по ID для текущего пользователя."""
    tasks_ref = get_collection_ref()
    if tasks_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    task_ref = tasks_ref.document(task_id)
    
    # Создаем словарь для обновления, включая только те поля, которые были предоставлены,
    # и исключая метаданные/ID.
    update_data = task_update.model_dump(exclude_unset=True, exclude={'id', 'created_at', 'updated_at'})
    
    if not update_data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

    update_data['updated_at'] = time.time()
    
    try:
        # Проверяем, существует ли документ, прежде чем пытаться обновить (опционально, но безопасно)
        if not task_ref.get().exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Task with ID {task_id} not found.")

        task_ref.update(update_data)
        
        # Получаем обновленный документ, чтобы вернуть полный объект
        updated_doc = task_ref.get()
        updated_data = updated_doc.to_dict()
        updated_task = Task(id=updated_doc.id, **updated_data)
        return updated_task
        
    except HTTPException:
        # Перебрасываем 404
        raise
    except Exception as e:
        logging.error(f"Error updating task {task_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update task.")


@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str):
    """Удаляет задачу по ID для текущего пользователя."""
    tasks_ref = get_collection_ref()
    if tasks_ref is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not initialized.")

    task_ref = tasks_ref.document(task_id)
    
    try:
        # Проверяем существование перед удалением
        if not task_ref.get().exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Task with ID {task_id} not found.")
            
        task_ref.delete()
        return {} # 204 No Content обычно возвращает пустое тело
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error deleting task {task_id}: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete task.")

# --- Уведомление о необходимости запуска через Uvicorn ---
if __name__ == '__main__':
    logging.warning("API должен запускаться с помощью Uvicorn, например: uvicorn app:app --host 0.0.0.0 --port 8000")
    logging.warning(f"Текущие заглушки: APP_ID={APP_ID}, USER_ID={USER_ID}. Измените их для реальной работы.")
    # Для локального тестирования
    # import uvicorn
    # uvicorn.run(app, host="0.0.0.0", port=8000)
