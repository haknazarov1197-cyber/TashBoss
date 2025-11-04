import os
import json
import time
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any

# --- ИМПОРТ И ИНИЦИАЛИЗАЦИЯ FIREBASE ---
# Примечание: Для работы в реальной среде (например, Render) вам понадобится
# настроить аутентификацию Firebase Admin SDK через переменные окружения.

try:
    # Используем импорты для работы с Firebase
    import firebase_admin
    from firebase_admin import credentials, firestore
    HAS_FIREBASE = True
except ImportError:
    # Если библиотеки нет, используем заглушку
    print("Warning: firebase-admin not installed. Data will not be persisted.")
    HAS_FIREBASE = False

db = None
auth = None
firebase_config = None
# Используем заглушку для ID приложения на случай, если переменная Canvas недоступна
app_id = "tashboss-app" 

# Получение глобальных переменных из Canvas
try:
    # __firebase_config и __app_id предоставляются Canvas
    firebase_config = json.loads(__firebase_config)
    app_id = __app_id
except NameError:
    print("Canvas global variables not found. Using local configuration.")

if HAS_FIREBASE and firebase_config:
    try:
        # Инициализация Firebase Admin SDK
        if isinstance(firebase_config, dict) and 'private_key' in firebase_config:
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            print("Firestore client initialized successfully.")
        else:
             print("Firebase config seems incomplete for Admin SDK. Skipping Admin init.")
             HAS_FIREBASE = False
    except Exception as e:
        print(f"Failed to initialize Firebase Admin SDK: {e}")
        HAS_FIREBASE = False


app = FastAPI()

# Разрешаем CORS, чтобы Mini App мог обращаться к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # В продакшене лучше ограничить
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- КОНФИГУРАЦИЯ ИГРЫ (ОТРАСЛИ) ---

# Ваши данные об отраслях, основанные на предоставленной таблице
INDUSTRIES_CONFIG = {
    "1": {"name": "Уборка улиц", "base_income": 1, "base_cost": 100, "cycle_time": 60, "base_cycle_time": 60},
    "2": {"name": "Коммунальные службы", "base_income": 3, "base_cost": 300, "cycle_time": 50, "base_cycle_time": 50},
    "3": {"name": "Транспорт", "base_income": 8, "base_cost": 1000, "cycle_time": 45, "base_cycle_time": 45},
    "4": {"name": "Парки и зоны отдыха", "base_income": 20, "base_cost": 3000, "cycle_time": 40, "base_cycle_time": 40},
    "5": {"name": "Малый бизнес", "base_income": 50, "base_cost": 8000, "cycle_time": 35, "base_cycle_time": 35},
    "6": {"name": "Заводы и фабрики", "base_income": 120, "base_cost": 20000, "cycle_time": 30, "base_cycle_time": 30},
    "7": {"name": "Качество воздуха", "base_income": 200, "base_cost": 50000, "cycle_time": 25, "base_cycle_time": 25},
    "8": {"name": "IT-парк", "base_income": 500, "base_cost": 120000, "cycle_time": 20, "base_cycle_time": 20},
    "9": {"name": "Туризм", "base_income": 1000, "base_cost": 250000, "cycle_time": 15, "base_cycle_time": 15},
    "10": {"name": "Международное сотрудничество", "base_income": 5000, "base_cost": 1000000, "cycle_time": 10, "base_cycle_time": 10},
}

# --- УТИЛИТЫ FIREBASE ---

def get_player_doc(user_id: str):
    """Получает ссылку на документ пользователя в Firestore."""
    if not db:
        return None
    # Используем путь для приватных данных: /artifacts/{appId}/users/{userId}/game_data/{docId}
    return db.collection("artifacts").document(app_id).collection("users").document(user_id).collection("game_data").document("player_state")


def get_default_player_state():
    """Возвращает начальное состояние игрока."""
    current_time = int(time.time())
    
    # Инициализируем все секторы с 0 уровнем (не куплено)
    initial_sectors = {}
    for k in INDUSTRIES_CONFIG:
        # Для начала, пусть первые два будут куплены, чтобы интерфейс не был пустым (как на скриншоте)
        initial_level = 1 if k in ["1", "2"] else 0
        initial_sectors[k] = {
            "level": initial_level, 
            "last_collect_time": current_time,
            "is_responsible_assigned": False
        }

    return {
        "balance": 100, # Начальный баланс
        "sectors": initial_sectors,
        "created_at": current_time
    }

# --- ОСНОВНАЯ ЛОГИКА ИГРЫ ---

def calculate_income_and_time(player_state: Dict[str, Any], sector_id: str) -> Dict[str, Any]:
    """
    Рассчитывает накопленный доход и обновляет время последнего сбора.

    Возвращает: {income: int, new_last_collect_time: int}
    """
    sector = player_state["sectors"].get(sector_id)
    config = INDUSTRIES_CONFIG.get(sector_id)

    # Если сектора нет, или он не куплен (уровень 0)
    if not sector or not config or sector["level"] == 0:
        return {"income": 0, "new_last_collect_time": sector["last_collect_time"] if sector else int(time.time())}

    # Параметры сектора на текущем уровне
    income_per_cycle = config["base_income"] * sector["level"]
    # В этой версии игры время цикла фиксировано, но оно может уменьшаться при улучшении
    cycle_time = config["cycle_time"] 

    current_time = int(time.time())
    
    # 1. Время простоя (сколько прошло с последнего сбора)
    idle_time = current_time - sector["last_collect_time"]
    
    # 2. Количество полных циклов, прошедших с последнего сбора
    cycles_passed = idle_time // cycle_time
    
    # 3. Накопленный доход
    income_to_collect = cycles_passed * income_per_cycle
    
    # 4. Обновление времени последнего сбора: сдвигаем его на количество собранных циклов
    # Это позволяет избежать сбора дохода за неполный цикл
    new_last_collect_time = sector["last_collect_time"] + (cycles_passed * cycle_time)

    # Если сбора не произошло (cycles_passed == 0), время не сдвигаем
    if cycles_passed == 0:
         new_last_collect_time = sector["last_collect_time"]

    return {
        "income": income_to_collect,
        "new_last_collect_time": new_last_collect_time
    }

def calculate_upgrade_cost(sector_id: str, level: int) -> int:
    """Рассчитывает стоимость следующего улучшения."""
    config = INDUSTRIES_CONFIG.get(sector_id)
    if not config:
        return 999999999 # Невозможно купить
    
    # Простое экспоненциальное увеличение стоимости: BaseCost * (Уровень^1.2)
    # Это позволит сделать улучшения дороже с каждым уровнем
    return int(config["base_cost"] * (level ** 1.2))

# --- API ENDPOINTS (Для Web App) ---

@app.get("/webapp", response_class=HTMLResponse)
async def serve_webapp():
    """Отдает HTML-файл для Web App (заглушка)."""
    # Этот эндпоинт служит для открытия Web App из Telegram
    return """
    <html>
        <head>
            <title>TashBoss Mini App Backend</title>
            <script src="https://telegram.org/js/telegram-web-app.js"></script>
        </head>
        <body class="bg-gray-100 p-8 text-center">
            <h1 class="text-2xl font-bold mb-4">TashBoss Mini App API</h1>
            <p class="mb-4">Этот эндпоинт используется для запуска Web App через Telegram.</p>
            <p id="user-info">Ожидание инициализации WebApp...</p>
            <button onclick="fetchData()" class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">
                Загрузить Мои Данные (Тест API)
            </button>
            <pre id="data" class="mt-4 p-4 bg-white border rounded text-left overflow-auto"></pre>
            <script>
                const tg = Telegram.WebApp;
                if (tg) {
                    tg.ready();
                    tg.expand();
                    const USER_ID = tg.initDataUnsafe.user?.id || 'DEBUG_USER_999';
                    document.getElementById('user-info').innerText = 'Telegram ID: ' + USER_ID;
                    const API_BASE_URL = window.location.origin;

                    window.fetchData = async function() {
                        document.getElementById('data').innerText = 'Загрузка...';
                        try {
                            const response = await fetch(`${API_BASE_URL}/api/load_state?user_id=${USER_ID}`);
                            const data = await response.json();
                            document.getElementById('data').innerText = JSON.stringify(data, null, 2);
                        } catch (error) {
                            document.getElementById('data').innerText = 'Ошибка загрузки данных: ' + error.message;
                        }
                    }
                }
            </script>
            <script src="https://cdn.tailwindcss.com"></script>
        </body>
    </html>
    """

@app.get("/api/load_state")
async def load_state(user_id: str):
    """
    Загружает состояние игрока из базы данных и рассчитывает накопленную прибыль.
    """
    if not HAS_FIREBASE or not db:
        return JSONResponse({"error": "Database not initialized. Cannot load state."}, status_code=500)

    doc_ref = get_player_doc(user_id)
    doc = doc_ref.get()

    if doc.exists:
        player_state = doc.to_dict()
    else:
        # Создание нового игрока
        player_state = get_default_player_state()
        doc_ref.set(player_state) # Сохраняем начальное состояние

    # Рассчитываем накопленную прибыль для всех секторов
    accumulated_income = 0
    sectors_data_for_app = {}

    for sector_id, sector_data in player_state["sectors"].items():
        # Если уровень > 0 (отрасль куплена)
        if sector_data["level"] > 0:
            result = calculate_income_and_time(player_state, sector_id)
            
            # Добавляем накопленный доход к общему балансу, но не обновляем БД
            # Это доход "к сбору"
            sector_data["income_to_collect"] = result["income"]
            accumulated_income += result["income"]
        
        # Добавляем стоимость улучшения для рендеринга
        sector_data["next_upgrade_cost"] = calculate_upgrade_cost(sector_id, sector_data["level"])
        
        sectors_data_for_app[sector_id] = sector_data


    # Обновляем состояние игрока для отправки в Mini App
    player_state["sectors"] = sectors_data_for_app
    player_state["total_accumulated_income"] = accumulated_income
    player_state["industries_config"] = INDUSTRIES_CONFIG # Отправляем конфигурацию для рендеринга

    return JSONResponse(player_state)

@app.post("/api/collect_income")
async def collect_income(request: Request):
    """
    Обрабатывает запрос на сбор дохода от Web App.
    """
    if not HAS_FIREBASE or not db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)

    try:
        data = await request.json()
        user_id = str(data.get("user_id"))
        sector_id = str(data.get("sector_id"))
    except Exception:
        return JSONResponse({"error": "Invalid request format"}, status_code=400)

    doc_ref = get_player_doc(user_id)
    doc = doc_ref.get()

    if not doc.exists:
        return JSONResponse({"error": "User state not found"}, status_code=404)

    player_state = doc.to_dict()
    sector_data = player_state["sectors"].get(sector_id)
    
    if not sector_data or sector_data["level"] == 0:
        return JSONResponse({"error": "Sector not purchased or not found"}, status_code=400)

    # 1. Рассчитываем, сколько можно собрать
    result = calculate_income_and_time(player_state, sector_id)
    income_to_collect = result["income"]
    new_last_collect_time = result["new_last_collect_time"]

    if income_to_collect > 0:
        # 2. Обновляем баланс и время сбора
        player_state["balance"] += income_to_collect
        player_state["sectors"][sector_id]["last_collect_time"] = new_last_collect_time

        # 3. Сохраняем обновленное состояние в Firestore
        doc_ref.set(player_state)

        return JSONResponse({
            "success": True, 
            "collected": income_to_collect, 
            "new_balance": player_state["balance"]
        })
    else:
        # Возвращаем 200, но с сообщением, что сбора нет
        return JSONResponse({"success": False, "message": "No income ready to collect"}, status_code=200)

@app.post("/api/upgrade_sector")
async def upgrade_sector(request: Request):
    """
    Обрабатывает запрос на улучшение сектора.
    """
    if not HAS_FIREBASE or not db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)

    try:
        data = await request.json()
        user_id = str(data.get("user_id"))
        sector_id = str(data.get("sector_id"))
    except Exception:
        return JSONResponse({"error": "Invalid request format"}, status_code=400)

    doc_ref = get_player_doc(user_id)
    doc = doc_ref.get()

    if not doc.exists:
        return JSONResponse({"error": "User state not found"}, status_code=404)

    player_state = doc.to_dict()
    sector_data = player_state["sectors"].get(sector_id)
    
    if not sector_data:
        return JSONResponse({"error": "Sector not found"}, status_code=404)

    current_level = sector_data["level"]
    # Если уровень 0, это означает покупку
    if current_level == 0:
        cost = INDUSTRIES_CONFIG[sector_id]["base_cost"]
    else:
        cost = calculate_upgrade_cost(sector_id, current_level)

    # Проверка баланса
    if player_state["balance"] < cost:
        return JSONResponse({"success": False, "message": "Insufficient balance"}, status_code=400)

    # 1. Проводим транзакцию
    player_state["balance"] -= cost
    player_state["sectors"][sector_id]["level"] += 1
    
    # При улучшении мы также "собираем" всю накопленную прибыль
    # Это предотвращает эксплойты и упрощает логику, но в данной реализации
    # мы просто оставляем последнее время сбора как есть, так как улучшение
    # не является сбором.
    
    # 2. Сохраняем обновленное состояние в Firestore
    doc_ref.set(player_state)

    return JSONResponse({
        "success": True, 
        "new_level": player_state["sectors"][sector_id]["level"],
        "new_balance": player_state["balance"],
        "cost": cost
    })
