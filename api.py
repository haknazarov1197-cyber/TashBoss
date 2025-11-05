import os
import json
import logging
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify
from firebase_admin import initialize_app, credentials, firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot

# --- НАСТРОЙКА ---
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Глобальные переменные, которые будут инициализированы позже
db = None
ADMIN_ID = "test_user_for_debug"
COLLECTION_PATH = f"artifacts/tashboss-1bd35/users/{ADMIN_ID}/game_state"

# --- КОНФИГУРАЦИЯ ИГРЫ (Должна совпадать с фронтендом) ---
INCOME_RATES = {
    "sector1": 0.5, 
    "sector2": 2.0, 
    "sector3": 10.0
}
SECTOR_COSTS = {
    "sector1": 100.0, 
    "sector2": 500.0, 
    "sector3": 2500.0
}
COST_MULTIPLIER = 1.15
STARTING_BALANCE = 5000.0
MAX_IDLE_TIME = 10 * 24 * 3600 # 10 дней в секундах
# --------------------------------------------------------

def calculate_cost(sector_name, current_level):
    """Рассчитывает стоимость следующего уровня сектора."""
    base_cost = SECTOR_COSTS.get(sector_name, 0)
    cost = base_cost * (COST_MULTIPLIER ** current_level)
    return round(cost, 2)

def calculate_income(sectors):
    """Рассчитывает общий доход в секунду."""
    total_income = 0
    for sector, level in sectors.items():
        total_income += INCOME_RATES.get(sector, 0) * level
    return total_income

def get_user_id(func):
    """Декоратор для извлечения user_id из заголовков или использования заглушки."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Используем жестко заданный ID для отладки
        user_id = ADMIN_ID 
        return func(user_id, *args, **kwargs)
    return wrapper

@app.before_request
def initialize_firebase():
    """Инициализация Firebase и Firestore при первом запросе."""
    global db
    if db is None:
        try:
            # Извлекаем JSON-строку из переменной окружения
            firebase_service_key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
            if not firebase_service_key:
                logging.error("❌ FIREBASE_SERVICE_ACCOUNT_KEY не установлен.")
                return jsonify({"status": "error", "detail": "Ключ Firebase не найден"}), 500

            # Парсим JSON-строку
            key_data = json.loads(firebase_service_key)
            
            # Убеждаемся, что databaseId корректен (используем tashboss)
            # В отличие от Admin SDK, для Firestore Admin Client не нужно явно указывать database_id при credentials
            # Но для уверенности, проверяем, что Project ID совпадает
            logging.info(f"✅ Проект Firestore: {key_data.get('project_id')}. База данных: tashboss.")

            # Инициализация приложения
            cred = credentials.Certificate(key_data)
            initialize_app(cred, {'databaseURL': f"https://{key_data.get('project_id')}.firebaseio.com"})
            
            # Инициализация Firestore, указывая database_id
            db = firestore.client(database="tashboss")
            logging.info("✅ Firestore Client инициализирован.")
        except Exception as e:
            logging.error(f"❌ Ошибка инициализации Firebase/Firestore: {e}")
            db = None # Сброс, чтобы повторить попытку при следующем запросе

# --- Вспомогательные функции Firestore ---

def get_state_document(user_id):
    """Возвращает ссылку на документ состояния пользователя."""
    return db.collection(COLLECTION_PATH).document(user_id)

def load_game_state_from_db(user_id):
    """Загружает состояние игры из Firestore или возвращает начальное."""
    doc_ref = get_state_document(user_id)
    snapshot: DocumentSnapshot = doc_ref.get()
    
    if snapshot.exists:
        data = snapshot.to_dict()
        logging.info(f"✅ Состояние загружено для {user_id}: Баланс {data['balance']:.2f}")
        return data
    else:
        # Начальное состояние
        initial_state = {
            "balance": STARTING_BALANCE,
            "sectors": {"sector1": 0, "sector2": 0, "sector3": 0},
            "last_collection_time": datetime.now().isoformat()
        }
        logging.info(f"🆕 Создано начальное состояние для {user_id}: Баланс {initial_state['balance']:.2f}")
        
        # Попытка сохранить начальное состояние (только если оно не существует)
        try:
            doc_ref.set(initial_state)
            logging.info("✅ Начальное состояние успешно сохранено.")
        except Exception as e:
            logging.error(f"❌ Ошибка сохранения начального состояния: {e}")
            
        return initial_state

def save_game_state_to_db(user_id, state):
    """Сохраняет текущее состояние игры в Firestore."""
    try:
        # Важно: Firebase Admin SDK сохраняет объекты datetime
        # Но поскольку мы используем ISO-строки, проблем быть не должно
        # Преобразуем объект `state` в чистый словарь, если он еще не таковой
        state_to_save = {
            "balance": state["balance"],
            "sectors": state["sectors"],
            "last_collection_time": state["last_collection_time"],
        }
        
        doc_ref = get_state_document(user_id)
        doc_ref.set(state_to_save)
        logging.info(f"✅ Состояние успешно сохранено для {user_id}: Баланс {state['balance']:.2f}")
        return True
    except Exception as e:
        logging.error(f"❌ CRITICAL: Ошибка сохранения состояния для {user_id}: {e}")
        return False

# --- Игровая логика ---

def calculate_passive_income(state):
    """Рассчитывает и добавляет пассивный доход к балансу."""
    last_time = datetime.fromisoformat(state['last_collection_time'])
    now = datetime.now()
    
    time_delta = now - last_time
    total_seconds = time_delta.total_seconds()
    
    # Ограничение по времени простоя
    effective_seconds = min(total_seconds, MAX_IDLE_TIME)
    
    income_rate = calculate_income(state['sectors'])
    collected_income = income_rate * effective_seconds
    
    # Обновление состояния
    state['balance'] = round(state['balance'] + collected_income, 2)
    state['last_collection_time'] = now.isoformat()
    
    logging.info(f"💰 Собрано {collected_income:.2f} BSS за {effective_seconds:.0f} сек.")
    
    return state, collected_income

# --- ЭНДПОИНТЫ API ---

@app.route('/api/debug_info', methods=['GET'])
def debug_info():
    """Проверка статуса бэкенда и Firestore."""
    if db:
        try:
            # Попытка доступа к Firestore для проверки соединения
            test_doc_ref = db.collection('artifacts').document('tashboss-1bd35').get()
            db_check_result = "✅ Firestore (ID: tashboss) инициализирован и отвечает."
            status = "ok_ready"
            
            # Проверяем, существует ли документ, чтобы подтвердить доступ
            if test_doc_ref.exists:
                db_check_details = "DB Check OK (доступ к artifacts)."
            else:
                 db_check_details = "DB Check OK (создан тестовый запрос)."
            
        except Exception as e:
            db_check_result = f"❌ Ошибка подключения Firestore: {e}"
            db_check_details = f"Ошибка: {str(e)}"
            status = "error"
    else:
        status = "error"
        db_check_result = "❌ Firebase/Firestore не инициализирован."
        db_check_details = "Нет объекта DB."

    return jsonify({
        "status": status,
        "message": "✅ Бэкенд запущен и Firebase инициализирован.",
        "project_id_from_key": "tashboss-1bd35",
        "db_check_result": db_check_result,
        "db_check_details": db_check_details
    })


@app.route('/api/load_state', methods=['POST'])
@get_user_id
def load_state(user_id):
    """Загружает или создает состояние игры и возвращает его."""
    if not db:
        return jsonify({"status": "error", "detail": "Сервер не инициализирован"}), 500
        
    try:
        # При загрузке состояния автоматически собираем пассивный доход
        state = load_game_state_from_db(user_id)
        state, _ = calculate_passive_income(state)
        
        # Сохраняем обновленное состояние обратно в базу
        save_game_state_to_db(user_id, state)
        
        return jsonify({"status": "ok", "state": state})
    except Exception as e:
        logging.error(f"❌ Ошибка при загрузке состояния для {user_id}: {e}")
        return jsonify({"status": "error", "detail": "Внутренняя ошибка сервера при загрузке состояния."}), 500

@app.route('/api/collect_income', methods=['POST'])
@get_user_id
def collect_income(user_id):
    """Собирает пассивный доход и возвращает новое состояние."""
    if not db:
        return jsonify({"status": "error", "detail": "Сервер не инициализирован"}), 500

    try:
        state = load_game_state_from_db(user_id)
        state, collected = calculate_passive_income(state)
        
        # Сохранение обновленного состояния
        if save_game_state_to_db(user_id, state):
            return jsonify({
                "status": "ok", 
                "state": state, 
                "collected": collected
            })
        else:
            return jsonify({"status": "error", "detail": "Не удалось сохранить состояние после сбора."}), 500
            
    except Exception as e:
        logging.error(f"❌ Ошибка при сборе дохода для {user_id}: {e}")
        return jsonify({"status": "error", "detail": "Внутренняя ошибка сервера при сборе дохода."}), 500


@app.route('/api/buy_sector', methods=['POST'])
@get_user_id
def buy_sector(user_id):
    """Покупает следующий уровень сектора."""
    if not db:
        return jsonify({"status": "error", "detail": "Сервер не инициализирован"}), 500

    try:
        data = request.get_json()
        sector_name = data.get('sector')
        
        if sector_name not in SECTOR_COSTS:
            return jsonify({"status": "error", "detail": "Неизвестный сектор."}), 400
            
        # 1. Загрузка состояния (с автоматическим сбором дохода)
        state = load_game_state_from_db(user_id)
        
        current_level = state['sectors'].get(sector_name, 0)
        cost = calculate_cost(sector_name, current_level)
        
        # 2. Проверка возможности покупки
        if state['balance'] < cost:
            logging.warning(f"❌ {user_id} попытался купить {sector_name} (ур. {current_level}) за {cost:.2f}, но баланс {state['balance']:.2f} недостаточен.")
            return jsonify({"status": "error", "detail": "Недостаточно средств."}), 403
            
        # 3. Выполнение покупки
        state['balance'] = round(state['balance'] - cost, 2)
        state['sectors'][sector_name] = current_level + 1
        
        logging.info(f"✅ {user_id} купил {sector_name}. Новый баланс: {state['balance']:.2f}")

        # 4. Сохранение нового состояния
        if save_game_state_to_db(user_id, state):
            return jsonify({"status": "ok", "state": state})
        else:
            return jsonify({"status": "error", "detail": "Не удалось сохранить состояние после покупки."}), 500
            
    except Exception as e:
        # !!! ЭТО ВАЖНО ДЛЯ ОТЛАДКИ !!!
        logging.error(f"❌ CRITICAL: Ошибка при покупке сектора для {user_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "detail": f"Внутренняя ошибка сервера при покупке. Подробности: {str(e)}", "sector": sector_name}), 500

# Если запускается не через Gunicorn, а напрямую (для локального тестирования)
if __name__ == '__main__':
    # Эта часть не должна выполняться в Render, но полезна локально
    # Для Render используйте Gunicorn или другой WSGI-сервер
    # В Render, инициализация произойдет в @app.before_request
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
