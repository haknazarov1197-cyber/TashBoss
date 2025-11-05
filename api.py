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

# !!! КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ 1: Явно указываем Flask использовать текущую директорию (.) как статическую.
app = Flask(__name__, static_folder='.') 

# Глобальные переменные, которые будут инициализированы
db = None
ADMIN_ID = "test_user_for_debug"
PROJECT_ID = "tashboss-1bd35" # Жестко задаем для упрощения
COLLECTION_PATH = f"artifacts/{PROJECT_ID}/users/{ADMIN_ID}/game_state"

# --- КОНФИГУРАЦИЯ ИГРЫ ---
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
# -------------------------

# --- Инициализация Firebase (выполняется один раз при запуске Gunicorn) ---

def init_firebase():
    global db
    try:
        # Извлекаем JSON-строку из переменной окружения
        firebase_service_key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
        if not firebase_service_key:
            logging.error("❌ CRITICAL: FIREBASE_SERVICE_ACCOUNT_KEY не установлен.")
            return

        # Парсим JSON-строку
        key_data = json.loads(firebase_service_key)
        logging.info(f"✅ Проект Firestore: {key_data.get('project_id')}. База данных: tashboss.")

        # Инициализация приложения
        cred = credentials.Certificate(key_data)
        initialize_app(cred, {'databaseURL': f"https://{key_data.get('project_id')}.firebaseio.com"})
        
        # !!! КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Используем database_id вместо database !!!
        db = firestore.client(database_id="tashboss") 
        logging.info("✅ Firestore Client инициализирован.")
    except Exception as e:
        # Убедимся, что Flask знает об ошибке
        logging.error(f"❌ CRITICAL: Ошибка инициализации Firebase/Firestore: {e}", exc_info=True)
        db = None 

init_firebase()

# --- Вспомогательные функции и Декораторы ---

def calculate_cost(sector_name, current_level):
    """Рассчитывает стоимость следующего уровня сектора."""
    base_cost = SECTOR_COSTS.get(sector_name, 0)
    # Округляем до целого числа, как указано в ТЗ, чтобы избежать проблем с UI
    cost = base_cost * (COST_MULTIPLIER ** current_level)
    return round(cost)

def calculate_income(sectors):
    """Рассчитывает общий доход в секунду."""
    total_income = 0
    for sector, level in sectors.items():
        total_income += INCOME_RATES.get(sector, 0) * level
    return total_income

def get_user_id(func):
    """Декоратор для извлечения user_id (используем заглушку, так как аутентификации нет)."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Используем жестко заданный ID для отладки
        user_id = ADMIN_ID 
        return func(user_id, *args, **kwargs)
    return wrapper

# --- Функции для работы с БД ---

def get_state_document(user_id):
    """Возвращает ссылку на документ состояния пользователя."""
    # Используем путь, который включает ADMIN_ID в качестве документа
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
        
        # Попытка сохранить начальное состояние
        try:
            # Используем transaction/batch для гарантированной записи при первом запуске
            batch = db.batch()
            batch.set(doc_ref, initial_state)
            batch.commit()
            logging.info("✅ Начальное состояние успешно сохранено.")
        except Exception as e:
            logging.error(f"❌ Ошибка сохранения начального состояния: {e}")
            
        return initial_state

def save_game_state_to_db(user_id, state):
    """Сохраняет текущее состояние игры в Firestore."""
    try:
        # Преобразуем объект `state` в чистый словарь
        state_to_save = {
            "balance": state["balance"],
            "sectors": state["sectors"],
            "last_collection_time": state["last_collection_time"],
        }
        
        # Используем транзакцию для атомарного обновления
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref, new_state):
            # Простая запись, так как вся логика чтения/записи/изменения уже произошла на сервере
            transaction.set(doc_ref, new_state)

        doc_ref = get_state_document(user_id)
        transaction = db.transaction()
        update_in_transaction(transaction, doc_ref, state_to_save)
        
        logging.info(f"✅ Состояние успешно сохранено для {user_id}: Баланс {state['balance']:.2f}")
        return True
    except Exception as e:
        logging.error(f"❌ CRITICAL: Ошибка сохранения состояния для {user_id}: {e}", exc_info=True)
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

@app.route('/api/load_state', methods=['POST'])
@get_user_id
def load_state(user_id):
    """Загружает или создает состояние игры и возвращает его."""
    if db is None:
        return jsonify({"status": "error", "detail": "Сервер не инициализирован"}), 500
        
    try:
        # 1. Загрузка состояния
        state = load_game_state_from_db(user_id)
        # 2. Расчет дохода (при загрузке)
        state, _ = calculate_passive_income(state)
        # 3. Сохранение обновленного состояния обратно в базу
        save_game_state_to_db(user_id, state)
        
        return jsonify({"status": "ok", "state": state})
    except Exception as e:
        logging.error(f"❌ Ошибка при загрузке состояния для {user_id}: {e}")
        return jsonify({"status": "error", "detail": "Внутренняя ошибка сервера при загрузке состояния."}), 500

@app.route('/api/collect_income', methods=['POST'])
@get_user_id
def collect_income(user_id):
    """Собирает пассивный доход и возвращает новое состояние."""
    if db is None:
        return jsonify({"status": "error", "detail": "Сервер не инициализирован"}), 500

    try:
        state = load_game_state_from_db(user_id)
        state, collected = calculate_passive_income(state)
        
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
    if db is None:
        return jsonify({"status": "error", "detail": "Сервер не инициализирован"}), 500

    try:
        data = request.get_json()
        sector_name = data.get('sector')
        
        if sector_name not in SECTOR_COSTS:
            return jsonify({"status": "error", "detail": "Неизвестный сектор."}), 400
            
        state = load_game_state_from_db(user_id)
        
        current_level = state['sectors'].get(sector_name, 0)
        cost = calculate_cost(sector_name, current_level)
        
        if state['balance'] < cost:
            logging.warning(f"❌ {user_id} попытался купить {sector_name} (ур. {current_level}) за {cost:.2f}, но баланс {state['balance']:.2f} недостаточен.")
            return jsonify({"status": "error", "detail": "Недостаточно средств."}), 403
            
        state['balance'] = round(state['balance'] - cost, 2)
        state['sectors'][sector_name] = current_level + 1
        
        logging.info(f"✅ {user_id} купил {sector_name}. Новый баланс: {state['balance']:.2f}")

        if save_game_state_to_db(user_id, state):
            return jsonify({"status": "ok", "state": state})
        else:
            return jsonify({"status": "error", "detail": "Не удалось сохранить состояние после покупки."}), 500
            
    except Exception as e:
        logging.error(f"❌ CRITICAL: Ошибка при покупке сектора для {user_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "detail": f"Внутренняя ошибка сервера при покупке. Подробности: {str(e)}", "sector": sector_name}), 500


@app.route('/bot_webhook', methods=['POST'])
def bot_webhook():
    """
    Обработчик для вебхука Telegram.
    Это минимальная заглушка, чтобы бот перестал получать 405 и мог работать.
    Здесь должна быть логика обработки команд Telegram (например, /start).
    """
    try:
        data = request.get_json(silent=True)
        if data:
            # Логируем, чтобы увидеть, что бот отправляет
            if 'message' in data and 'text' in data['message']:
                logging.info(f"🤖 Получено сообщение от бота: {data['message']['text']} (Chat ID: {data['message']['chat']['id']})")
            else:
                logging.info(f"🤖 Получено обновление от бота: {json.dumps(data)}")

        # Telegram ожидает 200 OK
        return jsonify({"status": "ok", "description": "Update received and processed."}), 200
        
    except Exception as e:
        logging.error(f"❌ Ошибка в обработчике вебхука: {e}", exc_info=True)
        # В случае ошибки возвращаем 200, чтобы Telegram не спамил повторными запросами
        return jsonify({"status": "error", "description": "Webhook error"}), 200

# !!! Секция обслуживания статических файлов остается без изменений !!!
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_index(path):
    """Обслуживание статического файла index.html и других ресурсов."""
    
    if path == '':
        return app.send_static_file('index.html')
    else:
        return app.send_static_file(path)


if __name__ == '__main__':
    # Эта часть не должна выполняться в Render
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
