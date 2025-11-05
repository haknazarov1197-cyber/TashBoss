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
// Множитель стоимости для экспоненциального роста. (1.15 - стандарт для кликеров)
const COST_MULTIPLIER = 1.15;

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
 * Вычисляет стоимость следующего уровня сектора.
 * @param {string} sector - Название сектора.
 * @param {number} currentLevel - Текущий уровень сектора.
 * @returns {number} Стоимость следующего уровня.
 */
function calculateNextCost(sector, currentLevel) {
    const baseCost = SECTOR_BASE_COSTS[sector];
    // Расчет следующей стоимости: Базовая стоимость * (Множитель в степени текущего уровня)
    // Уровень 0: 100 * 1.15^0 = 100
    // Уровень 1: 100 * 1.15^1 = 115
    // Уровень 2: 100 * 1.15^2 = 132.25
    return baseCost * Math.pow(COST_MULTIPLIER, currentLevel);
}

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
        
        // --- ИЗМЕНЕНИЕ: Использование новой функции для расчета стоимости ---
        const currentCost = calculateNextCost(sector, level);
        const incomeRate = SECTOR_RATES[sector];

        // Обновление UI уровня и стоимости
        document.getElementById(`${sector}-level`).textContent = level;
        document.getElementById(`${sector}-cost`).textContent = currentCost.toFixed(0);

        // Обновление кнопки покупки
        const buyButton = document.querySelector(`.buy-button[data-sector="${sector}"]`);
        if (buyButton) {
            // Обновление атрибута data-cost для использования в API (для удобства, хотя API должен считать сам)
            buyButton.setAttribute('data-cost', currentCost.toFixed(2));
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
            // Внимание: это сообщение может сработать, если доход был, но меньше 0.01.
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
        // --- ИЗМЕНЕНИЕ: Расчет ожидаемой стоимости для отображения в модальном окне ---
        const expectedCost = calculateNextCost(sector, gameState.sectors[sector]).toFixed(2);

        const data = await apiRequest('/buy_sector', { sector: sector });

        gameState = {
            balance: parseFloat(data.state.balance),
            sectors: data.state.sectors,
            last_collection_time: data.state.last_collection_time // Время сбора также обновляется
        };
        
        renderState();
        // Модальное окно теперь показывает, сколько было потрачено
        showModal("Успех", `Вы успешно купили ${sector} за ${expectedCost} BSS!`);

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
