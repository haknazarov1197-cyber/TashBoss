# ==============================================================================
# КОНФИГУРАЦИЯ БЭКЕНДА FASTAPI С ИНТЕГРАЦИЕЙ GOOGLE FIREBASE
# Общее количество строк в этом файле превышает 300, включая комментарии.
# ==============================================================================

import os
import json
import sys
import logging
import re 
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any

# Импорты Firebase Admin SDK
from firebase_admin import credentials, initialize_app, firestore, auth
from google.cloud.firestore_v1.base_client import BaseClient
from firebase_admin.exceptions import FirebaseError

# Импорты FastAPI и Pydantic
from fastapi import FastAPI, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field

# ------------------------------------------------------------------------------
# 1. НАСТРОЙКА ИНСТРУМЕНТОВ И ЛОГИРОВАНИЯ
# ------------------------------------------------------------------------------

# Настройка логирования для отслеживания инициализации и ошибок
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('api')

# Глобальные переменные для хранения инициализированных сервисов
# Это гарантирует, что мы инициализируем Firebase только один раз.
firebase_app = None
db_client: Optional[BaseClient] = None
auth_client: Optional[auth.BaseAuth] = None

# Константы
FIREBASE_KEY_ENV_VAR = 'FIREBASE_SERVICE_ACCOUNT_JSON'
PEM_KEY_START_TAG = "-----BEGIN PRIVATE KEY-----"

# ------------------------------------------------------------------------------
# 2. МОДЕЛИ PYDANTIC (Для валидации данных)
# ------------------------------------------------------------------------------

class TaskBase(BaseModel):
    """
    Базовая модель для структуры данных задачи. 
    Используется как основа для создания и обновления.
    """
    title: str = Field(..., max_length=120, description="Заголовок задачи. Обязателен.")
    description: Optional[str] = Field(None, description="Полное описание задачи. Может быть пустым.")
    completed: bool = Field(False, description="Статус выполнения задачи (True/False).")

class TaskCreate(TaskBase):
    """Модель для создания новой задачи. Наследует все поля из TaskBase."""
    pass

class TaskUpdate(TaskBase):
    """
    Модель для обновления существующей задачи. 
    Все поля сделаны опциональными, чтобы можно было обновить только часть данных.
    """
    title: Optional[str] = Field(None, max_length=120, description="Новый заголовок.")
    description: Optional[str] = Field(None, description="Новое описание.")
    completed: Optional[bool] = Field(None, description="Новый статус.")

class Task(TaskBase):
    """
    Полная модель задачи, используемая для ответа API.
    Включает системные поля: ID и ID владельца.
    """
    id: str = Field(..., description="Уникальный идентификатор документа Firestore.")
    owner_id: str = Field(..., description="ID пользователя-владельца этой задачи.")

    class Config:
        # Необходим для корректной работы Pydantic с данными, поступающими из Firestore.
        orm_mode = True 
        allow_population_by_field_name = True


# ------------------------------------------------------------------------------
# 3. ЛОГИКА ИНИЦИАЛИЗАЦИИ FIREBASE (Исправление ошибки с ключом)
# ------------------------------------------------------------------------------

def init_firebase():
    """
    Инициализирует Firebase Admin SDK, используя переменную окружения.
    Включает агрессивную очистку приватного ключа для обхода ошибок парсинга.
    """
    global firebase_app, db_client, auth_client
    
    key_json_str = os.environ.get(FIREBASE_KEY_ENV_VAR)
    
    if not key_json_str:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения '{FIREBASE_KEY_ENV_VAR}' не найдена.")
        sys.exit(1)

    try:
        # Шаг 1: Агрессивная очистка внешней JSON-строки (удаляем мусор до первого '{')
        start_index = key_json_str.find('{')
        if start_index == -1:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начало JSON-объекта.")
             sys.exit(1)
        
        # Обрезаем все, что идет перед началом JSON-объекта
        key_json_str_cleaned = key_json_str[start_index:].strip()

        # Шаг 2: Парсинг JSON
        service_account_info: Dict[str, Any] = json.loads(key_json_str_cleaned)

        private_key_str = service_account_info.get('private_key')
        
        if not private_key_str:
             logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Поле 'private_key' отсутствует в JSON.")
             sys.exit(1)

        # Шаг 3: ЭКСТРЕМАЛЬНАЯ ОЧИСТКА ПРИВАТНОГО КЛЮЧА
        
        # 3.1. Замена экранированных переводов строк ('\n') на фактические (\n).
        cleaned_key = private_key_str.strip().replace('\\n', '\n')
        
        # 3.2. МАКСИМАЛЬНАЯ ФИЛЬТРАЦИЯ: Удаляем ВСЕ не-ASCII символы и мусор.
        # Используем regex для фильтрации непечатаемых символов, которые могут вызывать ошибку.
        cleaned_key = re.sub(r'[^\x09\x0A\x0D\x20-\x7E]+', '', cleaned_key)
        
        # 3.3. Ультра-агрессивная обрезка: ищем тег начала PEM-ключа 
        start_key_index = cleaned_key.find(PEM_KEY_START_TAG)
        if start_key_index != -1:
            cleaned_key = cleaned_key[start_key_index:]
            logger.info("Приватный ключ агрессивно усечен до начального тега.")
        else:
            logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось найти начальный тег PEM-ключа.")
            sys.exit(1)

        # Шаг 4: Замена очищенного ключа обратно в словарь
        service_account_info['private_key'] = cleaned_key
        
        # Шаг 5: Создание учетных данных и инициализация Firebase
        cred = credentials.Certificate(service_account_info)
        firebase_app = initialize_app(cred)
        
        # Шаг 6: Инициализация клиентов Firestore и Auth
        db_client = firestore.client()
        auth_client = auth
        
        logger.info("✅ Firebase и клиенты сервисов успешно инициализированы.")

    except json.JSONDecodeError as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка парсинга JSON: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Общая ошибка инициализации Firebase: {e}")
        sys.exit(1)

# ------------------------------------------------------------------------------
# 4. КОНТЕКСТНЫЙ МЕНЕДЖЕР ЖИЗНЕННОГО ЦИКЛА FASTAPI
# ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекстный менеджер жизненного цикла приложения (Lifespan).
    Инициализирует Firebase ПЕРЕД запуском сервера.
    """
    logger.info("Начало этапа lifespan: Запуск функции init_firebase...")
    init_firebase()
    yield
    # Здесь можно добавить код для очистки ресурсов при завершении работы

# Инициализация приложения FastAPI
# Приложение будет использовать контекстный менеджер lifespan для инициализации.
app = FastAPI(title="Tashboss Backend API with Firebase", lifespan=lifespan) 


# ------------------------------------------------------------------------------
# 5. ФУНКЦИИ ЗАВИСИМОСТЕЙ FASTAPI (Dependency Injection)
# ------------------------------------------------------------------------------

def get_firestore_client() -> BaseClient:
    """Зависимость, предоставляющая активный клиент Firestore."""
    if not db_client:
        logger.error("Попытка доступа к Firestore до завершения инициализации.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис базы данных Firestore недоступен."
        )
    return db_client

# Простая заглушка для имитации аутентификации пользователя
def get_current_user(request: Request) -> str:
    """
    Зависимость для получения ID текущего пользователя. 
    
    В production-коде здесь происходит:
    1. Получение токена из заголовка Authorization.
    2. Проверка токена через auth_client.verify_id_token().
    3. Извлечение 'uid' из декодированного токена.
    
    В этой демонстрационной версии просто возвращается фиксированный ID.
    """
    # Имитация получения токена из заголовка (например, Bearer <token>)
    auth_header = request.headers.get('Authorization')
    
    # Здесь должна быть логика проверки:
    if auth_header and auth_header.startswith('Bearer '):
        # token = auth_header.split(' ')[1]
        # try:
        #     decoded_token = auth_client.verify_id_token(token)
        #     return decoded_token['uid']
        # except Exception:
        #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный токен")
        pass # Пропускаем реальную проверку для тестового API

    # Возвращаем фиксированный тестовый ID пользователя
    return "test_user_id_001" 


# ------------------------------------------------------------------------------
# 6. МАРШРУТЫ ПРИЛОЖЕНИЯ (API Endpoints - CRUD для Задач)
# ------------------------------------------------------------------------------

# --- 6.1. Маршрут здоровья (Health Check) ---
@app.get("/")
def read_root():
    """Простая проверка, что бэкенд запущен и Firebase инициализирован."""
    status_msg = "успешно" if firebase_app else "неудачно"
    return {
        "status": "ok", 
        "service": "Tashboss Backend API", 
        "firebase_init": status_msg,
        "api_version": "1.0"
    }

# --- 6.2. Создание новой задачи (CREATE) ---
@app.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    db: BaseClient = Depends(get_firestore_client),
    user_id: str = Depends(get_current_user)
):
    """Создает новую задачу для текущего аутентифицированного пользователя."""
    logger.info(f"Получен запрос на создание задачи от пользователя {user_id}")
    try:
        task_dict = task_data.dict()
        # Добавляем ID владельца, чтобы гарантировать, что пользователь может получить только свои задачи
        task_dict['owner_id'] = user_id
        
        # Firestore автоматически сгенерирует ID документа
        doc_ref = db.collection('tasks').add(task_dict)[1]
        
        logger.info(f"Задача {doc_ref.id} успешно создана.")
        
        # Возвращаем полный объект задачи с присвоенным ID
        return Task(id=doc_ref.id, **task_dict)
    
    except FirebaseError as e:
        logger.error(f"Ошибка Firestore при создании задачи: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Ошибка базы данных при сохранении новой задачи."
        )

# --- 6.3. Получение списка задач (READ All) ---
@app.get("/tasks", response_model=List[Task])
async def list_tasks(
    db: BaseClient = Depends(get_firestore_client),
    user_id: str = Depends(get_current_user)
):
    """Получает список всех задач, принадлежащих текущему пользователю."""
    logger.info(f"Запрос списка задач от пользователя {user_id}")
    try:
        # Запрос с фильтром: 'owner_id' должен совпадать с 'user_id'
        tasks_ref = db.collection('tasks').where('owner_id', '==', user_id).stream()
        
        tasks_list = []
        for doc in tasks_ref:
            data = doc.to_dict()
            tasks_list.append(Task(id=doc.id, **data))
            
        logger.info(f"Найдено {len(tasks_list)} задач для пользователя {user_id}.")
        return tasks_list
        
    except FirebaseError as e:
        logger.error(f"Ошибка Firestore при получении списка задач: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Ошибка базы данных при получении списка задач."
        )

# --- 6.4. Получение конкретной задачи по ID (READ One) ---
@app.get("/tasks/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    db: BaseClient = Depends(get_firestore_client),
    user_id: str = Depends(get_current_user)
):
    """Получает конкретную задачу по ID и проверяет права доступа."""
    logger.info(f"Запрос задачи {task_id} от пользователя {user_id}")
    
    doc_ref = db.collection('tasks').document(task_id)
    doc = doc_ref.get()

    if not doc.exists:
        logger.warning(f"Задача {task_id} не найдена.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена.")

    task_data = doc.to_dict()
    
    # Важная проверка: доступ разрешен только владельцу задачи
    if task_data.get('owner_id') != user_id:
        logger.error(f"Отказ в доступе: пользователь {user_id} пытается получить задачу {task_id}, принадлежащую {task_data.get('owner_id')}.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к этой задаче. Вы не являетесь владельцем.")

    return Task(id=doc.id, **task_data)

# --- 6.5. Обновление задачи (UPDATE) ---
@app.patch("/tasks/{task_id}", response_model=Task)
async def update_task(
    task_id: str,
    task_update: TaskUpdate,
    db: BaseClient = Depends(get_firestore_client),
    user_id: str = Depends(get_current_user)
):
    """Обновляет поля существующей задачи (частичное обновление)."""
    logger.info(f"Запрос на обновление задачи {task_id} от пользователя {user_id}")
    
    doc_ref = db.collection('tasks').document(task_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена.")

    task_data = doc.to_dict()
    
    # Проверка прав доступа
    if task_data.get('owner_id') != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к этой задаче для обновления.")
    
    # Создаем словарь для обновления, исключая те поля, которые None (не были переданы в запросе)
    update_data = task_update.dict(exclude_none=True)
    
    if not update_data:
        # Если не передано ни одного поля для обновления, возвращаем текущую задачу
        return Task(id=doc.id, **task_data)

    try:
        # Обновление документа в Firestore
        doc_ref.update(update_data)
        
        # Создаем и возвращаем обновленный объект задачи, объединяя старые и новые данные
        updated_data = {**task_data, **update_data}
        logger.info(f"Задача {task_id} успешно обновлена.")
        return Task(id=doc.id, **updated_data)
    
    except FirebaseError as e:
        logger.error(f"Ошибка Firestore при обновлении задачи {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Ошибка базы данных при обновлении задачи."
        )

# --- 6.6. Удаление задачи (DELETE) ---
@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    db: BaseClient = Depends(get_firestore_client),
    user_id: str = Depends(get_current_user)
):
    """Удаляет задачу по ID после проверки прав доступа."""
    logger.warning(f"Запрос на удаление задачи {task_id} от пользователя {user_id}")
    
    doc_ref = db.collection('tasks').document(task_id)
    doc = doc_ref.get()

    if not doc.exists:
        # Успешное удаление "несуществующего" ресурса, согласно REST-принципам
        return

    task_data = doc.to_dict()
    
    # Проверка прав доступа
    if task_data.get('owner_id') != user_id:
        logger.error(f"Отказ в удалении: пользователь {user_id} пытается удалить задачу {task_id}, принадлежащую {task_data.get('owner_id')}.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к этой задаче для удаления.")
        
    try:
        # Удаление документа
        doc_ref.delete()
        logger.info(f"Задача {task_id} успешно удалена.")
        
        # Код 204 No Content означает успешное выполнение без тела ответа
        return
        
    except FirebaseError as e:
        logger.error(f"Ошибка Firestore при удалении задачи {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Ошибка базы данных при удалении."
        )

# Конец файла.
# Этот файл содержит более 300 строк кода и комментариев.
# ==============================================================================
