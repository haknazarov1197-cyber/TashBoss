const BASE_API_URL = 'https://tashboss.onrender.com/api'; // ВАШЕ МЕСТО: замените на полный URL вашего Render-сервиса + /api

let currentState = {
    balance: 0,
    sectors: {
        sector1: 0,
        sector2: 0,
        sector3: 0
    },
    last_collection_time: new Date().toISOString()
};

const INCOME_RATES = {
    "sector1": 0.5, 
    "sector2": 2.0, 
    "sector3": 10.0
};
const SECTOR_COSTS = {
    "sector1": 100.0, 
    "sector2": 500.0, 
    "sector3": 2500.0
};

// --- DOM Элементы ---
const balanceDisplay = document.getElementById('balance-display');
const sectorContainer = document.getElementById('sector-container');
const shopContainer = document.getElementById('shop-container');
const messageBox = document.getElementById('message-box');
const messageText = document.getElementById('message-text');
const messageClose = document.getElementById('message-close');

// --- Утилиты ---
function showMessage(text, isError = false) {
    messageText.textContent = text;
    messageBox.className = `fixed inset-x-0 bottom-4 mx-auto p-4 max-w-sm rounded-lg shadow-2xl transition-opacity duration-300 ${isError ? 'bg-red-600' : 'bg-green-600'} opacity-100`;
    setTimeout(() => {
        messageBox.classList.remove('opacity-100');
        messageBox.classList.add('opacity-0');
    }, 4000);
}

// Форматирование числа с двумя знаками после запятой
function formatNumber(num) {
    return (Math.floor(num * 100) / 100).toFixed(2);
}

// --- API Запросы ---

// Получение ID Token из Telegram WebApp
function getAuthToken() {
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.auth_date) {
        // Мы используем initData как ID Token, это стандартный паттерн для Mini Apps
        return window.Telegram.WebApp.initData; 
    }
    // Заглушка для отладки, если нет Telegram среды
    return "debug_token_123"; 
}

async function apiCall(endpoint, data = {}) {
    const token = getAuthToken();
    const url = `${BASE_API_URL}/${endpoint}`;
    
    // Показываем индикатор загрузки Telegram
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.showProgress();
    }

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // КРИТИЧЕСКИ ВАЖНО: Передача токена для аутентификации FastAPI
                'Authorization': `Bearer ${token}` 
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error("API Call Failed:", error);
        showMessage(`Ошибка: ${error.message}`, true);
        throw error; // Перебрасываем ошибку для обработки во внешней функции
    } finally {
        // Скрываем индикатор загрузки Telegram
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.hideProgress();
        }
    }
}

// --- Главная логика игры ---

// 1. Загрузка состояния
async function loadState() {
    try {
        // Используем apiCall для первой точки взаимодействия с бэкендом
        const result = await apiCall('load_state');
        if (result.status === 'ok') {
            updateState(result.state);
            renderSectors();
            renderShop();
        }
    } catch (error) {
        // Это первая точка отказа. Если она не пройдет, игра зависнет.
        console.error("Failed to load state on startup. Check Firebase Key/CORS/Auth.", error);
        showMessage("Не удалось загрузить данные игры. Проверьте настройки сервера.", true);
    }
}

// 2. Обновление состояния и интерфейса
function updateState(newState) {
    currentState.balance = parseFloat(newState.balance || 0);
    currentState.sectors = newState.sectors || currentState.sectors;
    currentState.last_collection_time = newState.last_collection_time;
    
    balanceDisplay.textContent = formatNumber(currentState.balance);
    renderSectors();
    renderShop();
}

// 3. Расчет и отображение дохода в реальном времени
function calculateIncome(state) {
    const lastTime = new Date(state.last_collection_time);
    const now = new Date();
    const deltaSeconds = (now - lastTime) / 1000;
    
    // Ограничиваем максимальное время простоя 10 днями, чтобы избежать эксплойтов
    const MAX_IDLE_TIME = 10 * 24 * 3600; 
    const effectiveDeltaSeconds = Math.min(deltaSeconds, MAX_IDLE_TIME);

    let income = 0;
    for (const sector in state.sectors) {
        const count = state.sectors[sector];
        const rate = INCOME_RATES[sector];
        income += rate * count * effectiveDeltaSeconds;
    }
    return income;
}

function updateRealTimeDisplay() {
    // Проверка, что баланс отображается до того, как мы пытаемся его обновить
    if (!balanceDisplay) return; 

    const income = calculateIncome(currentState);
    const totalBalance = currentState.balance + income;
    balanceDisplay.textContent = formatNumber(totalBalance);
}

// 4. Сбор дохода
async function collectIncome() {
    try {
        const result = await apiCall('collect_income');
        if (result.status === 'ok') {
            const collected = result.state.collected_income;
            updateState(result.state);
            showMessage(`💰 Собрано: +${formatNumber(collected)} BSS!`);
        }
    } catch (error) {
        console.error("Failed to collect income:", error);
    }
}

// 5. Покупка сектора
async function buySector(sectorName) {
    try {
        const cost = SECTOR_COSTS[sectorName];
        if (currentState.balance < cost) {
            showMessage("🚫 Недостаточно средств!", true);
            return;
        }

        const result = await apiCall('buy_sector', { sector: sectorName });
        
        if (result.status === 'ok') {
            updateState(result.state);
            showMessage(`🎉 Куплен ${sectorName}. Стоимость: -${formatNumber(cost)} BSS.`);
        }
    } catch (error) {
        // Ошибка может прийти от FastAPI (например, ValueError "Insufficient balance")
        console.error("Failed to buy sector:", error);
    }
}

// --- Рендеринг UI ---

function renderSectors() {
    if (!sectorContainer) return;
    sectorContainer.innerHTML = '';
    const totalSectors = currentState.sectors.sector1 + currentState.sectors.sector2 + currentState.sectors.sector3;

    if (totalSectors === 0) {
        sectorContainer.innerHTML = '<p class="text-center text-gray-500 italic py-4">У вас нет активных секторов. Купите что-нибудь в магазине!</p>';
        return;
    }

    // Вывод информации о текущих секторах
    for (const [sector, count] of Object.entries(currentState.sectors)) {
        if (count > 0) {
            const rate = INCOME_RATES[sector];
            const name = sector.charAt(0).toUpperCase() + sector.slice(1);
            
            const div = document.createElement('div');
            div.className = 'bg-gray-700 p-4 rounded-xl shadow-md flex justify-between items-center mb-3';
            div.innerHTML = `
                <div>
                    <p class="text-lg font-bold">${name} (x${count})</p>
                    <p class="text-sm text-gray-400">Доход: ${formatNumber(rate * count)} BSS/сек</p>
                </div>
                <button onclick="collectIncome()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg shadow-lg transition duration-150 transform hover:scale-105">
                    Собрать
                </button>
            `;
            sectorContainer.appendChild(div);
        }
    }
}

function renderShop() {
    if (!shopContainer) return;
    shopContainer.innerHTML = '';
    
    // Рендеринг доступных к покупке секторов
    for (const [sector, cost] of Object.entries(SECTOR_COSTS)) {
        const name = sector.charAt(0).toUpperCase() + sector.slice(1);
        const rate = INCOME_RATES[sector];
        const canAfford = currentState.balance >= cost;
        
        const div = document.createElement('div');
        div.className = `bg-gray-700 p-4 rounded-xl shadow-md flex justify-between items-center mb-3 ${canAfford ? '' : 'opacity-50'}`;
        
        div.innerHTML = `
            <div>
                <p class="text-lg font-bold">${name}</p>
                <p class="text-sm text-gray-400">Доход: ${formatNumber(rate)} BSS/сек</p>
                <p class="text-sm text-yellow-400">Цена: ${formatNumber(cost)} BSS</p>
            </div>
            <button 
                id="buy-${sector}"
                onclick="buySector('${sector}')" 
                ${canAfford ? '' : 'disabled'}
                class="font-bold py-2 px-4 rounded-lg shadow-lg transition duration-150 transform hover:scale-105 ${canAfford ? 'bg-green-600 hover:bg-green-700 text-white' : 'bg-gray-500 text-gray-300 cursor-not-allowed'}"
            >
                Купить
            </button>
        `;
        shopContainer.appendChild(div);
    }
}

// --- Инициализация ---

// Главная функция запуска
function initializeApp() {
    // 1. Настройка Telegram WebApp
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
        // Включаем виброотклик
        window.Telegram.WebApp.onEvent('mainButtonClicked', () => window.Telegram.WebApp.HapticFeedback.impactOccurred('medium'));
    }

    // 2. Установка слушателя для сбора дохода по кнопке
    const collectButton = document.getElementById('collect-income-button');
    if (collectButton) {
        collectButton.addEventListener('click', collectIncome);
    }

    // 3. Загрузка данных
    loadState();

    // 4. Интервал обновления интерфейса (для отображения дохода в реальном времени)
    // Убедитесь, что DOM загружен, прежде чем искать элементы
    if (balanceDisplay) {
         setInterval(updateRealTimeDisplay, 100); // Обновляем баланс 10 раз в секунду
    }
}

// Запуск после загрузки DOM
window.onload = initializeApp;
