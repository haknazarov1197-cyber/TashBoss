// Глобальный объект для хранения данных игры
let gameState = null;

// Аутентификация: Токен ID Firebase, полученный после успешного входа
let authToken = null; 
let currentUserId = null;

// !!! КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: БАЗОВЫЙ URL API !!!
// Используем window.location.origin, так как Render обслуживает и фронтенд, и API
// API находится по пути /api
const BASE_API_URL = `${window.location.origin}/api`; 

// --- Константы UI (должны совпадать с бэкендом) ---
const SECTORS_CONFIG_FRONTEND = {
    "sector1": {"name": "Сектор A (Киоски)", "passive_income": 0.5, "base_cost": 100},
    "sector2": {"name": "Сектор B (Кафе)", "passive_income": 2.0, "base_cost": 500},
    "sector3": {"name": "Сектор C (Офисы)", "passive_income": 10.0, "base_cost": 2500},
};

// --- DOM Элементы ---
const statusMessage = document.getElementById('statusMessage');
const gameContent = document.getElementById('gameContent');
const balanceDisplay = document.getElementById('balanceDisplay');
const sectorsContainer = document.getElementById('sectorsContainer');
const collectButton = document.getElementById('collectIncomeButton');
const userIdDisplay = document.getElementById('userIdDisplay');
const passiveIncomeDisplay = document.getElementById('passiveIncomeDisplay');


// --- Утилиты для UI ---

function showTemporaryMessage(message, isError = false) {
    const banner = document.getElementById('messageBanner');
    banner.textContent = message;
    
    // Устанавливаем стили
    banner.className = `p-3 mb-4 rounded-lg shadow-lg text-white ${isError ? 'bg-red-600' : 'bg-green-600'}`; 
    
    // Сбрасываем opacity перед показом
    banner.style.opacity = 1; 
    banner.style.display = 'block';
    
    // Плавное исчезновение
    setTimeout(() => {
        banner.style.opacity = 0;
        setTimeout(() => {
            banner.style.display = 'none';
        }, 500); // Совпадает с CSS transition duration
    }, 4000);
}

/**
 * Рассчитывает стоимость следующего уровня сектора.
 * Стоимость = BaseCost * (Текущий_Уровень + 1)
 */
function calculateNextLevelCost(sectorId, currentLevel) {
    const config = SECTORS_CONFIG_FRONTEND[sectorId];
    if (!config) return 0;
    return config.base_cost * (currentLevel + 1);
}

function updateUI() {
    if (!gameState) return;

    // --- Обновление верхней панели ---
    const balance = (gameState.balance || 0);
    const availableIncome = (gameState.available_income || 0);
    
    // Форматирование валюты
    const formatter = new Intl.NumberFormat('ru-RU', { 
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
    
    // BossCoin (BC)
    balanceDisplay.textContent = formatter.format(balance) + ' BC';
    
    // Расчет общего пассивного дохода в секунду
    const totalIncome = Object.entries(gameState.sectors).reduce((sum, [key, level]) => {
        const incomePerLevel = SECTORS_CONFIG_FRONTEND[key]?.passive_income || 0;
        return sum + (incomePerLevel * level);
    }, 0);
    
    passiveIncomeDisplay.textContent = `Пассивный доход/сек: ${totalIncome.toFixed(2)} BC`;

    // --- Обновление кнопки сбора ---
    const incomeToCollect = parseFloat(availableIncome.toFixed(2));
    collectButton.textContent = `Собрать доход (${formatter.format(incomeToCollect)} BC)`;
    
    if (incomeToCollect >= 0.01) { 
        collectButton.disabled = false;
        collectButton.classList.remove('bg-gray-500', 'cursor-not-allowed');
        collectButton.classList.add('bg-yellow-500', 'hover:bg-yellow-600');
    } else {
        collectButton.disabled = true;
        collectButton.classList.add('bg-gray-500', 'cursor-not-allowed');
        collectButton.classList.remove('bg-yellow-500', 'hover:bg-yellow-600');
    }

    // --- Обновление секций покупки ---
    Object.entries(SECTORS_CONFIG_FRONTEND).forEach(([sectorId, config]) => {
        const sectorElement = document.getElementById(`sector-card-${sectorId}`);
        if (!sectorElement) return;

        const currentLevel = gameState.sectors[sectorId] || 0;
        const nextLevelCost = calculateNextLevelCost(sectorId, currentLevel);

        sectorElement.querySelector('.sector-level').textContent = `Уровень: ${currentLevel}`;
        sectorElement.querySelector('.sector-income').textContent = `+${config.passive_income.toFixed(2)} BC/сек`;

        const buyButton = sectorElement.querySelector('.buy-button');
        buyButton.textContent = `Купить след. (${formatter.format(nextLevelCost)} BC)`;
        buyButton.dataset.cost = nextLevelCost;

        if (balance >= nextLevelCost) {
            buyButton.disabled = false;
            buyButton.classList.remove('bg-gray-400', 'cursor-not-allowed');
            buyButton.classList.add('bg-green-600', 'hover:bg-green-700');
        } else {
            buyButton.disabled = true;
            buyButton.classList.add('bg-gray-400', 'cursor-not-allowed');
            buyButton.classList.remove('bg-green-600', 'hover:bg-green-700');
        }
    });

    // Обновление таймера и ID
    const now = new Date();
    document.getElementById('timer-status').textContent = `Обновлено: ${now.toLocaleTimeString()} | User ID: ${gameState.user_id}`;
    // Показываем укороченный UID
    userIdDisplay.textContent = currentUserId.substring(0, 8) + '...';

    gameContent.classList.remove('hidden');
    statusMessage.classList.add('hidden');
}

// --- API Запросы с Аутентификацией ---

async function apiCall(endpoint, method = 'POST', body = null) {
    if (!authToken) {
        showTemporaryMessage('Ошибка: Пользователь не аутентифицирован. Перезапустите WebApp.', true);
        return null;
    }

    const headers = {
        'Content-Type': 'application/json',
        // Используем токен Firebase ID для аутентификации на бэкенде
        'Authorization': `Bearer ${authToken}` 
    };

    const config = { method, headers };

    if (body) {
        config.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(`${BASE_API_URL}${endpoint}`, config);
        const data = await response.json();

        if (!response.ok) {
            const errorMessage = data.detail || 'Неизвестная ошибка сервера';
            showTemporaryMessage(`Ошибка [${response.status}]: ${errorMessage}`, true);
            console.error(`API Error on ${endpoint}:`, data);
            return null;
        }
        
        return data;
    } catch (error) {
        showTemporaryMessage(`Сетевая ошибка: Не удалось подключиться к серверу.`, true);
        console.error(`Fetch Error on ${endpoint}:`, error);
        return null;
    }
}

// --- Функции Игры ---

async function loadGameState() {
    // Вызываем API для загрузки состояния
    const data = await apiCall('/load_state'); 
    if (data) {
        gameState = data;
        updateUI();
    }
}

async function handleCollectIncome() {
    collectButton.disabled = true;
    showTemporaryMessage('Сбор дохода...');
    
    const data = await apiCall('/collect_income');
    
    if (data) {
        const collected = data.collected_amount || 0;
        gameState = data;
        updateUI();
        if (collected >= 0.01) {
            showTemporaryMessage(`💰 Собрано ${collected.toFixed(2)} BossCoin!`);
        } else {
            showTemporaryMessage('Пока нечего собирать.');
        }
    }
    collectButton.disabled = false;
}

async function handleBuySector(sectorId) {
    const sectorElement = document.getElementById(`sector-card-${sectorId}`);
    const buyButton = sectorElement.querySelector('.buy-button');
    const cost = parseFloat(buyButton.dataset.cost);

    if (gameState.balance < cost) {
        showTemporaryMessage('Недостаточно BossCoin для покупки!', true);
        return;
    }
    
    buyButton.disabled = true;
    showTemporaryMessage(`Покупка ${SECTORS_CONFIG_FRONTEND[sectorId].name}...`);
    
    const data = await apiCall('/buy_sector', 'POST', { sector_id: sectorId });
    
    if (data) {
        if (data.purchase_successful) {
            gameState = data;
            updateUI();
            showTemporaryMessage(`✅ Покупка успешна! Новый уровень ${gameState.sectors[sectorId]}.`);
        } else {
            // Если баланс не прошел проверку на бэкенде (должно быть редко)
            showTemporaryMessage(`❌ Недостаточно средств для покупки!`, true);
        }

        // Если был собран пассивный доход перед покупкой, сообщаем об этом
        if (data.collected_amount > 0.01) {
            showTemporaryMessage(`(Доход ${data.collected_amount.toFixed(2)} BC собран перед покупкой)`, false);
        }
    }
    buyButton.disabled = false;
}

// --- Инициализация ---

document.addEventListener('DOMContentLoaded', () => {
    // 1. Инициализация UI для кнопок покупки
    Object.entries(SECTORS_CONFIG_FRONTEND).forEach(([key, config]) => {
        const sectorCard = document.createElement('div');
        sectorCard.id = `sector-card-${key}`;
        sectorCard.className = 'card bg-gray-800 p-4 mb-4 flex justify-between items-center';
        sectorCard.innerHTML = `
            <div class="text-left">
                <p class="text-lg font-bold">${config.name}</p>
                <p class="text-xs text-gray-400 sector-level">Уровень: 0</p>
                <p class="text-sm text-green-400 sector-income">+${config.passive_income.toFixed(2)} BC/сек</p>
            </div>
            <button class="buy-button bg-gray-400 text-white py-2 px-4 rounded-lg shadow-md transition duration-200 cursor-not-allowed" disabled data-cost="${config.base_cost}">
                Купить след. (${config.base_cost.toFixed(2)} BC)
            </button>
        `;
        sectorsContainer.appendChild(sectorCard);

        sectorCard.querySelector('.buy-button').addEventListener('click', () => handleBuySector(key));
    });

    // 2. Установка обработчиков основных событий
    collectButton.addEventListener('click', handleCollectIncome);
});

// Глобальная функция, вызываемая из index.html после успешной аутентификации.
window.initAppAfterAuth = (firebaseIdToken, userUID) => {
    authToken = firebaseIdToken;
    currentUserId = userUID; // Устанавливаем UID для отображения
    
    // После получения токена ID запускаем загрузку состояния
    loadGameState(); 

    // Запускаем цикл обновления (каждые 5 секунд)
    setInterval(loadGameState, 5000); 
}
