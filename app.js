// --- КОНФИГУРАЦИЯ ---
// КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Жестко заданный URL API для обхода проблем с относительными путями на Render.
const BASE_API_URL = 'https://tashboss.onrender.com/api'; 
const SECTOR_RATES = {
    "sector1": 0.5, 
    "sector2": 2.0, 
    "sector3": 10.0
};
const SECTOR_BASE_COSTS = {
    "sector1": 100.0, 
    "sector2": 500.0, 
    "sector3": 2500.0
};

// --- ГЛОБАЛЬНОЕ СОСТОЯНИЕ ---
let gameState = {
    balance: 0.0,
    sectors: {
        "sector1": 0,
        "sector2": 0,
        "sector3": 0
    },
    last_collection_time: new Date().toISOString()
};

// --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ UI ---

/**
 * Отображает всплывающее окно с сообщением (замена alert).
 * @param {string} title
 * @param {string} content
 */
function showModal(title, content) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-content').textContent = content;
    document.getElementById('message-modal').classList.remove('hidden');
    document.getElementById('message-modal').classList.add('flex');
}

/**
 * Получает токен авторизации для API.
 * @returns {string} Токен Bearer
 */
function getAuthToken() {
    // В Telegram Mini App, initData используется для верификации.
    // На бэкенде мы используем его как "токен" для заглушки UID.
    const initData = window.Telegram.WebApp.initData || '';
    return `Bearer ${initData}`;
}

/**
 * Выполняет запрос к API бэкенда.
 * @param {string} endpoint - /load_state, /collect_income, /buy_sector
 * @param {object | null} body - Тело запроса
 * @returns {Promise<object>} JSON-ответ от API
 */
async function apiRequest(endpoint, body = null) {
    const url = `${BASE_API_URL}${endpoint}`;
    const options = {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': getAuthToken()
        },
        body: body ? JSON.stringify(body) : null
    };

    try {
        const response = await fetch(url, options);
        const data = await response.json();

        if (!response.ok) {
            // Если ответ не 2xx, выбрасываем ошибку с сообщением от сервера
            const detail = data.detail || "Произошла ошибка на сервере.";
            throw new Error(detail);
        }
        return data;
    } catch (error) {
        console.error(`Ошибка запроса к ${endpoint}:`, error);
        showModal("Ошибка связи с сервером", error.message || `Не удалось подключиться к ${url}.`);
        throw error; // Перебрасываем для обработки в вызывающей функции
    }
}

// --- ОСНОВНАЯ ЛОГИКА ИГРЫ ---

/**
 * Обновляет отображение баланса и сектора.
 */
function renderState() {
    // 1. Обновление баланса
    document.getElementById('balance-display').textContent = gameState.balance.toFixed(2);
    
    let totalIncomeRate = 0;

    // 2. Обновление секций покупки и расчет общей ставки дохода
    for (const sector in gameState.sectors) {
        const level = gameState.sectors[sector];
        const baseCost = SECTOR_BASE_COSTS[sector];
        const currentCost = baseCost * (level + 1);
        const incomeRate = SECTOR_RATES[sector];

        // Обновление UI уровня и стоимости
        document.getElementById(`${sector}-level`).textContent = level;
        document.getElementById(`${sector}-cost`).textContent = currentCost.toFixed(0);

        // Обновление кнопки покупки
        const buyButton = document.querySelector(`.buy-button[data-sector="${sector}"]`);
        if (buyButton) {
            buyButton.disabled = gameState.balance < currentCost;
        }

        // Обновление общей ставки
        totalIncomeRate += incomeRate * level;
    }
    
    // 3. Обновление общей ставки дохода
    document.getElementById('income-rate').textContent = totalIncomeRate.toFixed(2);
    
    // 4. Разблокировка кнопки сбора, если есть пассивный доход
    document.getElementById('collect-button').disabled = totalIncomeRate === 0;

    // Скрытие загрузочного сообщения
    document.getElementById('status-message').classList.add('hidden');
}


/**
 * Загружает состояние игры при старте.
 */
async function loadGameState() {
    try {
        document.getElementById('status-message').textContent = 'Загрузка данных...';
        document.getElementById('status-message').classList.remove('hidden');

        const data = await apiRequest('/load_state');
        
        // Обновление глобального состояния
        gameState = {
            balance: parseFloat(data.state.balance),
            sectors: data.state.sectors,
            last_collection_time: data.state.last_collection_time
        };
        
        // Индикация собранного дохода (если был)
        if (data.collected_income > 0.01) {
            document.getElementById('collected-amount').textContent = data.collected_income.toFixed(2);
            document.getElementById('collected-info').classList.remove('hidden');
            setTimeout(() => {
                document.getElementById('collected-info').classList.add('hidden');
            }, 3000);
        }

        renderState();
        Telegram.WebApp.ready(); // Уведомляем Telegram, что приложение готово

    } catch (error) {
        // Ошибка уже показана в showModal через apiRequest
        document.getElementById('status-message').textContent = 'Ошибка загрузки. Проверьте подключение.';
        document.getElementById('status-message').classList.remove('hidden');
    }
}

/**
 * Обрабатывает сбор пассивного дохода.
 */
async function handleCollectIncome() {
    const button = document.getElementById('collect-button');
    const originalText = button.querySelector('#collect-text').textContent;
    button.disabled = true;
    button.querySelector('#collect-text').textContent = 'Сбор...';

    try {
        const data = await apiRequest('/collect_income');

        gameState = {
            balance: parseFloat(data.state.balance),
            sectors: data.state.sectors,
            last_collection_time: data.state.last_collection_time
        };

        renderState();
        
        // Показ собранной суммы
        if (data.collected_income > 0.01) {
            document.getElementById('collected-amount').textContent = data.collected_income.toFixed(2);
            document.getElementById('collected-info').classList.remove('hidden');
            setTimeout(() => {
                document.getElementById('collected-info').classList.add('hidden');
            }, 3000);
        } else {
            showModal("Успешно", "Доход был собран, но за это время не было накоплений.");
        }

    } catch (error) {
        // Ошибка уже показана
    } finally {
        button.querySelector('#collect-text').textContent = originalText;
        renderState(); // Перерисовать, чтобы обновить состояние кнопки
    }
}

/**
 * Обрабатывает покупку сектора.
 * @param {string} sector - Название сектора (sector1, sector2, sector3)
 */
async function handleBuySector(sector) {
    const button = document.querySelector(`.buy-button[data-sector="${sector}"]`);
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Покупка...';

    try {
        const data = await apiRequest('/buy_sector', { sector: sector });

        gameState = {
            balance: parseFloat(data.state.balance),
            sectors: data.state.sectors,
            last_collection_time: data.state.last_collection_time // Время сбора также обновляется
        };
        
        renderState();
        showModal("Успех", `Вы успешно купили ${sector}!`);

    } catch (error) {
        // Ошибка уже показана (например, недостаток средств)
    } finally {
        button.textContent = originalText;
        renderState(); // Перерисовать, чтобы обновить состояние кнопки
    }
}


// --- ИНИЦИАЛИЗАЦИЯ ---

/**
 * Настройка обработчиков событий и запуск приложения.
 */
function setupEventListeners() {
    // Обработчик кнопки сбора дохода
    document.getElementById('collect-button').addEventListener('click', handleCollectIncome);

    // Обработчик кнопок покупки (через делегирование)
    document.querySelectorAll('.buy-button').forEach(button => {
        button.addEventListener('click', () => {
            const sector = button.getAttribute('data-sector');
            handleBuySector(sector);
        });
    });
}

// Запуск приложения
document.addEventListener('DOMContentLoaded', () => {
    // Проверка, что Telegram WebApp SDK загружен
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) {
        Telegram.WebApp.ready();
        Telegram.WebApp.expand(); // Разворачиваем приложение на весь экран
        
        setupEventListeners();
        loadGameState(); // Начинаем загрузку состояния
    } else {
        showModal("Критическая Ошибка", "Не удалось загрузить Telegram WebApp SDK. Запустите в среде Telegram.");
    }
});
