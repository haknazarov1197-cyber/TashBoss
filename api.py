import time
import json
from fastapi import FastAPI, Request, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import os # Добавлено для работы с переменными окружения

# --- FIREBASE ИНИЦИАЛИЗАЦИЯ ---
# Используем глобальные переменные Canvas для конфигурации
try:
    from firebase_admin import initialize_app, firestore, credentials
    
    # Пытаемся получить APP_ID из переменных окружения (для Render) или глобальных переменных (для Canvas)
    app_id = os.environ.get('APP_ID') or globals().get('__app_id', 'default-app-id')
    
    # NOTE: В среде Render/Production лучше всего передавать учетные данные через
    # переменную окружения, которую FastAPI загрузит как JSON строку.
    if 'FIREBASE_CREDENTIALS_JSON' in os.environ:
        # Загрузка учетных данных из переменной окружения Render/Prod
        cred_json = json.loads(os.environ['FIREBASE_CREDENTIALS_JSON'])
        cred = credentials.Certificate(cred_json)
        firebase_app = initialize_app(cred)
    elif '__firebase_config' in globals() and globals()['__firebase_config']:
        # Инициализация с помощью переданной конфигурации (для среды Canvas)
        firebase_config = json.loads(globals()['__firebase_config'])
        try:
            # Пытаемся использовать переданный конфиг как Service Account
            cred = credentials.Certificate(firebase_config)
            firebase_app = initialize_app(cred)
        except Exception:
            # Fallback для тестовых сред
            firebase_app = initialize_app()
    else:
        # Локальная разработка
        firebase_app = initialize_app()

    db = firestore.client()
    print("Firestore Client Initialized.")
except ImportError:
    print("Firebase Admin not installed. Using mock database.")
    # МОК ДЛЯ ЛОКАЛЬНОЙ РАЗРАБОТКИ БЕЗ FIREBASE
    db = None
    app_id = 'local-dev-app-id'
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    db = None
    app_id = 'local-dev-app-id'

# --- КОНФИГУРАЦИЯ ИГРЫ (10 СЕКТОРОВ) ---
INDUSTRIES_CONFIG = {
    "chorsu_market": {"name": "1. Рынок Чорсу", "base_income": 1, "base_cost": 100, "base_cycle_time": 5},
    "transport": {"name": "2. Транспорт", "base_income": 2, "base_cost": 250, "base_cycle_time": 8},
    "communal": {"name": "3. Коммунальные службы", "base_income": 3, "base_cost": 500, "base_cycle_time": 10},
    "tourism": {"name": "4. Туризм", "base_income": 5, "base_cost": 1000, "base_cycle_time": 12},
    "ecology": {"name": "5. Экология", "base_income": 8, "base_cost": 2500, "base_cycle_time": 15},
    "infrastructure": {"name": "6. Инфраструктура", "base_income": 12, "base_cost": 5000, "base_cycle_time": 18},
    "air_quality": {"name": "7. Качество воздуха", "base_income": 18, "base_cost": 10000, "base_cycle_time": 22},
    "international": {"name": "8. Международное", "base_income": 25, "base_cost": 20000, "base_cycle_time": 25},
    "ict": {"name": "9. ИКТ и Цифра", "base_income": 35, "base_cost": 40000, "base_cycle_time": 30},
    "innovation": {"name": "10. Инновации", "base_income": 50, "base_cost": 80000, "base_cycle_time": 35}
}

# МАКСИМАЛЬНЫЙ УРОВЕНЬ
MAX_LEVEL = 100

def get_sector_params(sector_key: str, level: int) -> Dict[str, Any]:
    """
    Рассчитывает динамические параметры сектора (доход, стоимость, время цикла).
    """
    config = INDUSTRIES_CONFIG.get(sector_key)
    if not config or level <= 0:
        # Возвращаем базовые данные для некупленного сектора
        base_cost_for_buy = config["base_cost"] if config else 0
        return {"income": 0, "cost": base_cost_for_buy, "cycle_time": config["base_cycle_time"] if config else 0}

    # 1. Доход: Линейный рост
    income = config["base_income"] * level

    # 2. Стоимость улучшения: Экспоненциальный рост
    # Cost = Base_Cost * (Level ^ 1.5)
    # Примечание: Эта стоимость относится к ПЕРЕХОДУ на текущий 'level' (если 'level' > 1) 
    # или к ПОКУПКЕ следующего уровня (если 'level' используется для определения стоимости улучшения)
    cost = int(config["base_cost"] * (level ** 1.5))

    # 3. Время цикла: Уменьшение до 50% от базового времени при MAX_LEVEL
    # Уменьшение на 0.5% за каждый уровень
    reduction_factor = 1.0 - (0.5 * (level / MAX_LEVEL))
    cycle_time = max(1, int(config["base_cycle_time"] * reduction_factor)) 

    return {
        "income": income,
        "cost": cost,
        "cycle_time": cycle_time
    }

# --- МОДЕЛИ ДАННЫХ ---

class PlayerState(BaseModel):
    user_id: str
    balance: int
    total_income: int
    industries: Dict[str, Dict[str, Any]] # {"chorsu_market": {"level": 1, "last_collect": 1678886400}}

class CollectRequest(BaseModel):
    user_id: str
    sector_key: str

class UpgradeRequest(BaseModel):
    user_id: str
    sector_key: str

class CollectAllRequest(BaseModel):
    user_id: str

# --- ФУНКЦИИ БАЗЫ ДАННЫХ (Firestore) ---

def get_player_doc_ref(user_id: str):
    """Возвращает ссылку на документ игрока в Firestore."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not initialized.")
    # Путь: /artifacts/{appId}/users/{userId}/game_data/state
    return db.collection(f"artifacts/{app_id}/users/{user_id}/game_data").document("state")

def get_initial_player_state(user_id: str) -> PlayerState:
    """Возвращает начальное состояние игрока."""
    initial_industries = {}
    
    # 1. Инициализация всех секторов на уровне 0 с базовым временем цикла
    for key in INDUSTRIES_CONFIG:
        base_time = INDUSTRIES_CONFIG[key]['base_cycle_time']
        initial_industries[key] = {"level": 0, "last_collect": 0, "current_cycle_time": base_time}
    
    # 2. Игрок начинает с одним купленным сектором (Уровень 1)
    starter_key = "chorsu_market"
    starter_level = 1
    starter_params = get_sector_params(starter_key, starter_level)
    
    initial_industries[starter_key]["level"] = starter_level
    # Используем актуальное время цикла для уровня 1
    initial_industries[starter_key]["current_cycle_time"] = starter_params["cycle_time"]
    
    return PlayerState(
        user_id=user_id,
        balance=1000, # Начальный баланс увеличен
        total_income=0,
        industries=initial_industries
    )

def load_player_state(user_id: str) -> PlayerState:
    """Загружает состояние игрока из Firestore."""
    if not db:
        return get_initial_player_state(user_id) # В случае мока, возвращаем начальное
        
    doc_ref = get_player_doc_ref(user_id)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        # Добавляем новые секторы, если они появились в конфиге
        for key in INDUSTRIES_CONFIG:
            if key not in data.get('industries', {}):
                 data['industries'][key] = {"level": 0, "last_collect": 0, "current_cycle_time": INDUSTRIES_CONFIG[key]['base_cycle_time']}
        
        return PlayerState(**data)
    else:
        initial_state = get_initial_player_state(user_id)
        # Сохраняем начальное состояние
        doc_ref.set(initial_state.model_dump())
        return initial_state

def save_player_state(state: PlayerState):
    """Сохраняет состояние игрока в Firestore."""
    if not db:
        print("Mock Save: State not saved because DB is not initialized.")
        return
        
    doc_ref = get_player_doc_ref(state.user_id)
    doc_ref.set(state.model_dump())


# --- ЛОГИКА ИГРЫ ---

def calculate_income_and_update_state(state: PlayerState, current_time: float) -> PlayerState:
    """
    Рассчитывает накопленный доход для всех секторов и обновляет состояние.
    Не собирает доход, а только рассчитывает, сколько можно собрать.
    """
    for key, sector_data in state.industries.items():
        level = sector_data["level"]
        
        # Получаем параметры для ТЕКУЩЕГО уровня
        params = get_sector_params(key, level)
        cycle_time = params.get("cycle_time", 0)
        income_per_cycle = params.get("income", 0)

        if level > 0 and cycle_time > 0:
            last_collect = sector_data.get("last_collect", 0)

            if last_collect > 0:
                elapsed = current_time - last_collect
                # Сколько полных циклов прошло
                cycles_completed = int(elapsed / cycle_time)
                
                # Обновляем состояние сектора для фронтенда
                sector_data["income_to_collect"] = cycles_completed * income_per_cycle
                
                # Рассчитываем оставшееся время до следующего цикла
                time_in_current_cycle = elapsed % cycle_time
                sector_data["remaining_time"] = cycle_time - time_in_current_cycle
                
            else:
                # Если только что куплен (last_collect=0), считаем, что цикл только начался
                sector_data["income_to_collect"] = 0
                sector_data["remaining_time"] = cycle_time

    return state


# --- FASTAPI ИНИЦИАЛИЗАЦИЯ ---

app = FastAPI(title="TashBoss Game API")


@app.get("/webapp")
async def serve_webapp():
    """Отдает HTML-страницу Mini App. Это эндпоинт, который должен открывать бот."""
    # Полный HTML-код для index.html
    html_content = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TashBoss: Мини-Приложение</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'primary': '#0a5b8f', // Темно-синий
                        'secondary': '#10a08e', // Бирюзовый
                        'background': '#1f2937', // Темный фон
                        'card-bg': '#374151', // Фон карточек
                    },
                    fontFamily: {
                        sans: ['Inter', 'sans-serif'],
                    },
                }
            }
        }
    </script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        body {
            font-family: 'Inter', sans-serif;
            background-color: #1f2937;
            color: #f3f4f6;
            min-height: 100vh;
        }
        .card {
            background-color: #374151;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
        }
        .btn-primary {
            background-color: #10b981; /* Зеленый */
            color: white;
            transition: background-color 0.1s;
        }
        .btn-primary:hover:not(:disabled) {
            background-color: #059669;
        }
        .btn-secondary {
            background-color: #3b82f6; /* Синий */
            color: white;
            transition: background-color 0.1s;
        }
        .btn-secondary:hover:not(:disabled) {
            background-color: #2563eb;
        }
        .btn-disabled {
            background-color: #4b5563;
            color: #9ca3af;
            cursor: not-allowed;
        }
        .icon {
            width: 24px;
            height: 24px;
            display: inline-block;
            vertical-align: middle;
            margin-right: 8px;
        }
        .income-ready {
            border: 2px solid #10b981;
            animation: pulse-green 1.5s infinite;
        }
        @keyframes pulse-green {
            0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
            100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }
    </style>
</head>
<body class="p-4 sm:p-6 pb-20">
    <!-- Инициализация Telegram Web App -->
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    
    <div id="loading" class="text-center p-12 text-gray-400">
        <svg class="animate-spin h-8 w-8 text-secondary mx-auto mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        Загрузка данных TashBoss...
    </div>

    <div id="app-content" class="hidden max-w-2xl mx-auto">
        
        <!-- HEADER / BALANCE -->
        <header class="text-center mb-6 p-4 rounded-xl bg-card-bg shadow-lg">
            <h1 class="text-3xl font-bold text-secondary">TashBoss</h1>
            <p class="text-sm text-gray-400 mt-1">Симулятор градоначальника</p>
            <div class="mt-3">
                <p class="text-xl font-semibold">💰 Баланс: <span id="player-balance">0</span> BSS</p>
            </div>
            <div class="mt-4 p-2 bg-gray-600 rounded-lg">
                <h2 class="text-lg font-medium">Общий доход к сбору: <span id="total-income">0</span> BSS</h2>
            </div>
        </header>

        <!-- SECTORS LIST -->
        <main id="sectors-container" class="space-y-4"></main>
        
    </div>

    <!-- MAIN JAVASCRIPT LOGIC -->
    <script>
        const tg = window.Telegram.WebApp;
        tg.ready();
        
        let USER_ID = null;
        if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
            USER_ID = tg.initDataUnsafe.user.id;
        } else {
            // Заглушка для тестирования вне Telegram 
            USER_ID = 'TEST_USER_12345'; 
            console.warn("Using TEST_USER_ID. Run inside Telegram Web App for real user ID.");
        }

        // ВАЖНО: URL вашего развернутого FastAPI сервера
        const BASE_API_URL = window.location.origin;

        let gameState = {
            balance: 0,
            industries: {}
        };
        let updateInterval = null;

        // --- API HELPERS ---
        async function apiFetch(endpoint, method = 'GET', body = null) {
            const url = `${BASE_API_URL}/api/${endpoint}`;
            try {
                const response = await fetch(url, {
                    method: method,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: body ? JSON.stringify(body) : null,
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    // Отключаем MainButton при ошибке, чтобы он не мешал
                    tg.MainButton.hide(); 
                    throw new Error(errorData.detail || `Server error: ${response.status}`);
                }
                return await response.json();
            } catch (error) {
                console.error("API Fetch Error:", error.message);
                tg.showAlert(`Ошибка: ${error.message}`);
                return null;
            }
        }
        
        // --- GAME LOGIC FUNCTIONS ---

        function formatTime(seconds) {
            if (seconds <= 0) return 'Готово!';
            // Используем Math.floor, чтобы не отображать 0, пока не пройдет полная секунда
            return `${Math.floor(seconds)} сек.`; 
        }

        function renderSector(key, sectorData) {
            const container = document.getElementById('sectors-container');
            let sectorElement = document.getElementById(`sector-${key}`);

            if (!sectorElement) {
                sectorElement = document.createElement('div');
                sectorElement.id = `sector-${key}`;
                container.appendChild(sectorElement);
            }
            
            sectorElement.className = `card p-4 rounded-xl ${sectorData.income_to_collect > 0 ? 'income-ready' : ''}`;
            
            const config = sectorData.config; // Configuration from API
            const level = sectorData.level;
            const isOwned = level > 0;
            const nextCost = sectorData.cost;
            const income = sectorData.income;
            
            let statusHTML = '';
            let buttonsHTML = '';

            // Определяем оставшееся время для UI
            const remaining = sectorData.remaining_time || (sectorData.level > 0 ? sectorData.current_cycle_time : '—');
            
            if (isOwned) {
                
                statusHTML = `
                    <p class="text-lg font-bold text-secondary">${config.name} (Ур. ${level})</p>
                    <p class="text-sm text-gray-300">💰 Прибыль за цикл: ${income} BSS</p>
                    <p class="text-sm text-gray-300">⏱ Время цикла: ${sectorData.current_cycle_time} сек.</p>
                    <div class="mt-2 text-md">
                        <p class="text-yellow-300">Накоплено: ${sectorData.income_to_collect.toLocaleString()} BSS</p>
                        <p class="text-gray-400" id="timer-${key}">Осталось: ${formatTime(remaining)}</p>
                    </div>
                `;

                // Кнопки для купленного сектора
                buttonsHTML = `
                    <button class="btn-primary w-full sm:w-1/2 p-2 rounded-lg font-semibold" 
                            onclick="collectIncome('${key}')"
                            ${sectorData.income_to_collect === 0 ? 'disabled' : ''}>
                        📥 Собрать
                    </button>
                    <button class="btn-secondary w-full sm:w-1/2 p-2 rounded-lg font-semibold ml-0 sm:ml-2 mt-2 sm:mt-0" 
                            onclick="upgradeSector('${key}')"
                            ${gameState.balance < nextCost ? 'disabled' : ''}>
                        🚀 Улучшить (${nextCost.toLocaleString()} BSS)
                    </button>
                `;
            } else {
                // Кнопки для некупленного сектора
                const baseIncome = sectorData.income || sectorData.config.base_income;
                const baseCycleTime = sectorData.current_cycle_time;
                
                statusHTML = `
                    <p class="text-lg font-bold text-secondary">${config.name} (Не куплен)</p>
                    <p class="text-sm text-gray-300">Базовая прибыль (Ур. 1): ${baseIncome} BSS</p>
                    <p class="text-sm text-gray-300">Базовое время цикла: ${baseCycleTime} сек.</p>
                    <div class="mt-2 text-md">
                        <p class="text-yellow-300">Накоплено: 0 BSS</p>
                        <p class="text-gray-400">Осталось: —</p>
                    </div>
                `;

                buttonsHTML = `
                    <button class="btn-primary w-full p-2 rounded-lg font-semibold" 
                            onclick="upgradeSector('${key}', true)"
                            ${gameState.balance < nextCost ? 'disabled' : ''}>
                        🛒 Купить (${nextCost.toLocaleString()} BSS)
                    </button>
                `;
            }

            sectorElement.innerHTML = `
                ${statusHTML}
                <div class="mt-4 flex flex-col sm:flex-row justify-between">
                    ${buttonsHTML}
                </div>
            `;
            
        }

        function updateUI() {
            let totalIncome = 0;
            const sortedKeys = Object.keys(gameState.industries).sort((a, b) => {
                const indexA = parseInt(gameState.industries[a].config.name.split('.')[0]);
                const indexB = parseInt(gameState.industries[b].config.name.split('.')[0]);
                return indexA - indexB;
            });
            
            // 1. Render Sectors
            sortedKeys.forEach(key => {
                const sectorData = gameState.industries[key];
                if (sectorData.level > 0) {
                    totalIncome += sectorData.income_to_collect || 0;
                }
                renderSector(key, sectorData);
            });
            
            // 2. Update Main Headers
            document.getElementById('player-balance').textContent = gameState.balance.toLocaleString();
            document.getElementById('total-income').textContent = totalIncome.toLocaleString();

            // 3. Update Telegram MainButton
            if (totalIncome > 0) {
                tg.MainButton.setText(`📥 Собрать ВЕСЬ доход (${totalIncome.toLocaleString()} BSS)`).show().enable();
                // Обработчик MainButton устанавливается в INIT, поэтому здесь только показываем/обновляем
            } else {
                tg.MainButton.hide();
            }

            // 4. Show Content
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('app-content').classList.remove('hidden');
        }
        
        // --- API CALLS ---

        async function loadGameState() {
            if (!USER_ID) return;
            
            const data = await apiFetch(`load_state?user_id=${USER_ID}`);
            
            if (data) {
                gameState = data;
                
                // Секторы всегда приходят отсортированными и с актуальными параметрами
                
                // Запуск локального таймера, если он еще не запущен
                if (updateInterval === null) {
                    // Вызываем updateLocalTimers сразу, а затем по интервалу
                    updateLocalTimers(); 
                    updateInterval = setInterval(updateLocalTimers, 1000);
                }
                
                updateUI();
            }
        }

        async function collectIncome(sectorKey) {
            tg.MainButton.showProgress();
            const body = { user_id: USER_ID, sector_key: sectorKey };
            const result = await apiFetch('collect_income', 'POST', body);
            tg.MainButton.hideProgress();

            if (result) {
                tg.showNotification({ message: `✅ Собрано: ${result.collected_income.toLocaleString()} BSS!`, type: 'success' });
                // Перезагружаем состояние с сервера
                await loadGameState();
            }
        }

        async function collectAllIncome() {
            const body = { user_id: USER_ID };
            // Скрыть кнопку сразу, чтобы предотвратить двойное нажатие
            tg.MainButton.showProgress(); 
            
            const result = await apiFetch('collect_all_income', 'POST', body);
            
            tg.MainButton.hideProgress();
            
            if (result) {
                if (result.total_collected_income > 0) {
                    tg.showNotification({ message: `✅ Общий доход собран: ${result.total_collected_income.toLocaleString()} BSS!`, type: 'success' });
                } else {
                    tg.showAlert(`Нет готового дохода для сбора.`);
                }
                await loadGameState();
            }
        }

        async function upgradeSector(sectorKey, isPurchase = false) {
            const body = { user_id: USER_ID, sector_key: sectorKey };
            
            // Включаем индикатор загрузки, пока идет транзакция
            tg.MainButton.showProgress();
            
            const result = await apiFetch('upgrade_sector', 'POST', body);
            
            tg.MainButton.hideProgress();
            
            if (result) {
                let message = isPurchase 
                    ? `🎉 Сектор куплен! Ваш уровень: 1. `
                    : `🚀 Улучшение завершено! Теперь уровень: ${result.new_level}.`;
                
                tg.showNotification({ message: message, type: 'success' });
                // Перезагружаем состояние с сервера
                await loadGameState();
            }
        }

        // --- LOCAL TIMER LOGIC ---

        function updateLocalTimers() {
            const currentTimestamp = Date.now() / 1000;
            let totalIncome = 0;
            let needToRefresh = false;

            Object.keys(gameState.industries).forEach(key => {
                const sectorData = gameState.industries[key];
                
                if (sectorData.level > 0 && sectorData.last_collect > 0) {
                    const elapsed = currentTimestamp - sectorData.last_collect;
                    const cycleTime = sectorData.current_cycle_time;
                    const incomePerCycle = sectorData.income; // Используем income, рассчитанный API
                    
                    const cyclesCompleted = Math.floor(elapsed / cycleTime);
                    const incomeToCollect = cyclesCompleted * incomePerCycle;
                    
                    // Обновление UI для накопленного дохода
                    sectorData.income_to_collect = incomeToCollect;
                    totalIncome += incomeToCollect;

                    // Обновление UI для таймера
                    let remaining = Math.max(0, cycleTime - (elapsed % cycleTime));
                    sectorData.remaining_time = remaining;
                    
                    const timerElement = document.getElementById(`timer-${key}`);
                    if (timerElement) {
                        timerElement.textContent = `Осталось: ${formatTime(remaining)}`;
                    }

                    // Обновление состояния кнопки Собрать
                    const collectButton = document.querySelector(`#sector-${key} button:first-child`);
                    if (collectButton) {
                        const cardElement = document.getElementById(`sector-${key}`);

                        if (incomeToCollect > 0) {
                            collectButton.disabled = false;
                            cardElement.classList.add('income-ready');
                            // Если только что стало готово, нужно обновить кнопку MainButton
                            if (!tg.MainButton.isVisible) {
                                needToRefresh = true; 
                            }
                        } else {
                            collectButton.disabled = true;
                            cardElement.classList.remove('income-ready');
                        }
                    }
                }
            });
            
            // Обновление главного счетчика и кнопки
            document.getElementById('total-income').textContent = totalIncome.toLocaleString();

            if (totalIncome > 0) {
                tg.MainButton.setText(`📥 Собрать ВЕСЬ доход (${totalIncome.toLocaleString()} BSS)`).show().enable();
            } else {
                tg.MainButton.hide();
            }

            // Принудительное обновление UI, если MainButton только что появилась (чтобы обновить текст на карте)
            if (needToRefresh) {
                 updateUI();
            }
        }

        // --- INIT ---
        tg.onEvent('main_button_pressed', collectAllIncome);
        loadGameState();
    </script>
</body>
</html>
"""
    return Response(content=html_content, media_type="text/html")


@app.get("/api/load_state", response_model=PlayerState)
async def load_state_endpoint(user_id: str):
    """Эндпоинт для загрузки состояния игрока и расчета накопленного дохода."""
    current_time = time.time()
    state = load_player_state(user_id)
    
    # 1. Рассчитываем и обновляем состояние, чтобы фронтенд знал, сколько собирать
    # (Добавляет 'income_to_collect' и 'remaining_time' в state.industries)
    state = calculate_income_and_update_state(state, current_time)
    
    # 2. Добавляем актуальные параметры (доход, стоимость, цикл) в ответ для фронтенда
    for key, sector_data in state.industries.items():
        current_level = sector_data["level"]
        
        # Параметры для отображения ТЕКУЩЕГО уровня (доход, цикл)
        # Если уровень 0, используем уровень 1 для отображения базовой информации
        display_level = max(1, current_level) 
        current_params = get_sector_params(key, display_level)
        
        # Параметры для улучшения (стоимость для перехода на следующий уровень)
        next_level = max(1, current_level + 1)
        next_params = get_sector_params(key, next_level)
        
        # Передаем фронтенду:
        # - Доход текущего уровня
        sector_data["income"] = current_params["income"] 
        # - Стоимость улучшения до next_level
        sector_data["cost"] = next_params["cost"]
        # - Время цикла ТЕКУЩЕГО уровня (ВАЖНО для таймера фронтенда)
        # Если уровень 0, используем базовое время
        if current_level > 0:
            sector_data["current_cycle_time"] = current_params["cycle_time"]
        else:
             sector_data["current_cycle_time"] = INDUSTRIES_CONFIG[key]['base_cycle_time']
             
        sector_data["config"] = INDUSTRIES_CONFIG[key]
        
    return state


@app.post("/api/collect_income")
async def collect_income_endpoint(request: CollectRequest):
    """Эндпоинт для сбора дохода с одного сектора."""
    user_id = request.user_id
    sector_key = request.sector_key
    current_time = time.time()
    
    state = load_player_state(user_id)
    sector_data = state.industries.get(sector_key)
    
    if not sector_data or sector_data["level"] == 0:
        raise HTTPException(status_code=400, detail="Sector not owned or invalid.")

    level = sector_data["level"]
    params = get_sector_params(sector_key, level)
    cycle_time = params["cycle_time"]
    income_per_cycle = params["income"]
    last_collect = sector_data["last_collect"]

    elapsed = current_time - last_collect
    cycles_completed = int(elapsed / cycle_time)

    if cycles_completed == 0:
        raise HTTPException(status_code=400, detail="Income is not ready yet.")

    collected_income = cycles_completed * income_per_cycle
    
    # Обновляем состояние: добавляем доход и сбрасываем время сбора, 
    # чтобы отсчет нового цикла начался с момента current_time
    state.balance += collected_income
    
    # Сброс last_collect на текущее время
    sector_data["last_collect"] = current_time 
    
    save_player_state(state)
    
    return {"collected_income": collected_income, "new_balance": state.balance}


@app.post("/api/collect_all_income")
async def collect_all_income_endpoint(request: CollectAllRequest):
    """Эндпоинт для сбора дохода со ВСЕХ секторов."""
    user_id = request.user_id
    current_time = time.time()
    total_collected_income = 0
    
    state = load_player_state(user_id)
    
    for key, sector_data in state.industries.items():
        level = sector_data["level"]
        if level > 0:
            params = get_sector_params(key, level)
            cycle_time = params["cycle_time"]
            income_per_cycle = params["income"]
            last_collect = sector_data["last_collect"]

            elapsed = current_time - last_collect
            cycles_completed = int(elapsed / cycle_time)

            if cycles_completed > 0:
                collected_income = cycles_completed * income_per_cycle
                total_collected_income += collected_income
                
                # Обновляем время последнего сбора для этого сектора
                sector_data["last_collect"] = current_time
    
    if total_collected_income > 0:
        state.balance += total_collected_income
        save_player_state(state)
        
    return {"total_collected_income": total_collected_income, "new_balance": state.balance}


@app.post("/api/upgrade_sector")
async def upgrade_sector_endpoint(request: UpgradeRequest):
    """Эндпоинт для улучшения или покупки сектора."""
    user_id = request.user_id
    sector_key = request.sector_key
    current_time = time.time()
    
    state = load_player_state(user_id)
    sector_data = state.industries.get(sector_key)
    
    if not sector_data:
        raise HTTPException(status_code=400, detail="Invalid sector key.")

    current_level = sector_data["level"]
    
    # Уровень, который будет достигнут
    next_level = current_level + 1
    
    # Стоимость для перехода на next_level
    params = get_sector_params(sector_key, next_level)
    cost = params["cost"]
    
    if state.balance < cost:
        raise HTTPException(status_code=400, detail="Недостаточно BSS для покупки/улучшения.")

    # Вычитаем стоимость и увеличиваем уровень
    state.balance -= cost
    sector_data["level"] = next_level

    # ИСПРАВЛЕНИЕ: Обновляем last_collect: при любом улучшении (покупке или апгрейде) 
    # цикл должен начаться заново, чтобы обеспечить геймплейный баланс.
    sector_data["last_collect"] = current_time
    
    # Обновляем время цикла (оно зависит от нового уровня)
    sector_data["current_cycle_time"] = get_sector_params(sector_key, next_level)["cycle_time"]
    
    save_player_state(state)
    
    return {"new_level": next_level, "new_balance": state.balance}

# --- HEALTH CHECK ---
@app.get("/")
def read_root():
    return {"Hello": "TashBoss API is running!"}
