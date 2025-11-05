// Глобальный объект для хранения данных игры
let gameState = null;

// Аутентификация: предполагаем, что токен будет предоставлен из Telegram WebApp initDataUnsafe
let authToken = null; 
// Используем BASE_URL из ваших требований
const BASE_API_URL = 'https://tashboss.onrender.com/api'; 

// --- Константы UI (должны совпадать с бэкендом) ---
const SECTORS_CONFIG_FRONTEND = {
    "sector1": {"name": "Сектор А", "passive_income": 0.5},
    "sector2": {"name": "Сектор B", "passive_income": 2.0},
    "sector3": {"name": "Сектор C", "passive_income": 10.0},
};

// --- DOM Элементы ---
const statusMessage = document.getElementById('statusMessage');
const gameContent = document.getElementById('gameContent');
const balanceDisplay = document.getElementById('balanceDisplay');
const sectorsContainer = document.getElementById('sectorsContainer');
const collectButton = document.getElementById('collectIncomeButton');
const clickButton = document.getElementById('clickButton');
const userIdDisplay = document.getElementById('userIdDisplay');
const passiveIncomeDisplay = document.getElementById('passiveIncomeDisplay');


// --- Утилиты для UI ---

function showTemporaryMessage(message, isError = false) {
    const banner = document.getElementById('messageBanner');
    banner.textContent = message;
    banner.className = isError 
        ? 'p-3 mb-4 rounded-lg bg-red-600 text-white shadow-lg' 
        : 'p-3 mb-4 rounded-lg bg-green-600 text-white shadow-lg';
    banner.style.display = 'block';
    
    // Плавное исчезновение
    setTimeout(() => {
        banner.style.opacity = 0;
        setTimeout(() => {
            banner.style.display = 'none';
            banner.style.opacity = 1; // Сброс для следующего появления
        }, 500); 
    }, 4000);
}

function updateUI() {
    if (!gameState) return;

    // --- Обновление верхней панели ---
    const balance = (gameState.balance || 0);
    const availableIncome = (gameState.available_income || 0);
    
    balanceDisplay.textContent = new Intl.NumberFormat('ru-RU', { 
        style: 'currency', 
        currency: 'USD', // Имитация BossCoin
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(balance);
    
    const totalIncome = Object.entries(gameState.sectors).reduce((sum, [key, level]) => {
        const incomePerLevel = SECTORS_CONFIG_FRONTEND[key]?.passive_income || 0;
        return sum + (incomePerLevel * level);
    }, 0);
    
    passiveIncomeDisplay.textContent = `Пассивный доход/сек: ${totalIncome.toFixed(2)}`;

    // --- Обновление кнопки сбора ---
    const incomeToCollect = parseFloat(availableIncome.toFixed(2));
    collectButton.textContent = `Собрать доход (${incomeToCollect} BC)`;
    
    if (incomeToCollect > 0.01) { 
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
        
        // Стоимость покупки следующего уровня
        const baseCost = config.passive_income * 200; // Простая формула, так как base_cost не приходит
        const nextLevelCost = baseCost * (currentLevel + 1);

        sectorElement.querySelector('.sector-level').textContent = `Уровень: ${currentLevel}`;
        sectorElement.querySelector('.sector-income').textContent = `+${config.passive_income.toFixed(2)} BC/сек`;

        const buyButton = sectorElement.querySelector('.buy-button');
        buyButton.textContent = `Купить след. (${nextLevelCost.toFixed(2)} BC)`;
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

    // Обновление таймера (используется только для отладки, но полезно)
    const now = new Date();
    document.getElementById('timer-status').textContent = `Обновлено: ${now.toLocaleTimeString()}`;
}

// --- API Запросы с Аутентификацией ---

async function apiCall(endpoint, method = 'POST', body = null) {
    if (!authToken) {
        showTemporaryMessage('Ошибка: Пользователь не аутентифицирован.', true);
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
        showTemporaryMessage(`Сетевая ошибка: ${error.message}`, true);
        console.error(`Fetch Error on ${endpoint}:`, error);
        return null;
    }
}

// --- Функции Игры ---

async function loadGameState() {
    const data = await apiCall('/load_state');
    if (data) {
        gameState = data;
        updateUI();
        // В Firebase Admin SDK UID пользователя - это user_id, но для фронтенда 
        // нам достаточно знать, что он аутентифицирован.
        const userIdFromState = data.user_id || 'N/A';
        userIdDisplay.textContent = userIdFromState.substring(0, 8) + '...';
        gameContent.classList.remove('hidden');
        statusMessage.classList.add('hidden');
        
    }
}

async function handleCollectIncome() {
    collectButton.disabled = true;
    showTemporaryMessage('Сбор дохода...');
    
    const data = await apiCall('/collect_income');
    
    if (data) {
        gameState = data;
        updateUI();
        if (data.collected_amount > 0.01) {
            showTemporaryMessage(`💰 Собрано ${data.collected_amount.toFixed(2)} BossCoin!`);
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
        // Проверяем, произошла ли покупка (уровень увеличился)
        const oldLevel = gameState.sectors[sectorId] || 0;
        gameState = data;
        updateUI();
        
        if (gameState.sectors[sectorId] > oldLevel) {
            showTemporaryMessage(`✅ Покупка успешна! Новый уровень ${gameState.sectors[sectorId]}.`);
        } else {
            showTemporaryMessage(`❌ Недостаточно средств для покупки!`, true);
        }

        // Если был собран пассивный доход перед покупкой, сообщаем об этом
        if (data.collected_amount > 0.01) {
            showTemporaryMessage(`(Доход ${data.collected_amount.toFixed(2)} BC собран перед покупкой)`);
        }
    }
    buyButton.disabled = false;
}

// --- Обработчик Клика (для будущего расширения) ---
function handleUserClick() {
    // В текущей версии клик не отправляется на бэкенд, а просто дает визуальный эффект.
    // Реальный кликер должен использовать `/click` endpoint, но пока мы используем пассивный доход.
    showTemporaryMessage('+1 BC (Клик временно отключен, используйте пассивный доход)', false);
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
            <button class="buy-button bg-gray-400 text-white py-2 px-4 rounded-lg shadow-md transition duration-200 cursor-not-allowed" disabled data-cost="1000">
                Купить след. (1000 BC)
            </button>
        `;
        sectorsContainer.appendChild(sectorCard);

        sectorCard.querySelector('.buy-button').addEventListener('click', () => handleBuySector(key));
    });

    // 2. Установка обработчиков основных событий
    collectButton.addEventListener('click', handleCollectIncome);
    clickButton.addEventListener('click', handleUserClick);


    // 3. Главная функция запуска
    async function main() {
        statusMessage.textContent = "Инициализация Telegram WebApp...";
        
        if (typeof window.Telegram === 'undefined' || !window.Telegram.WebApp.initDataUnsafe) {
            statusMessage.textContent = "❌ Ошибка: Запустите приложение внутри Telegram через кнопку бота.";
            statusMessage.classList.add('text-red-500');
            return;
        }

        // Токен Firebase ID предоставляется через onAuthStateChanged в index.html (для старой версии)
        // Новая версия (где аутентификация перенесена на бэкенд)
        // Для WebApp мы используем initData как токен, который бэкенд верифицирует
        // и обменивает на Custom Token, который мы используем как Auth Bearer.

        // ВНИМАНИЕ: Поскольку в index.html мы используем Firebase SDK для аутентификации, 
        // нам нужен токен ID, который генерируется после входа.
        // Здесь мы используем заглушку, так как токен ID приходит из Firebase Auth
        // после успешного входа, который происходит в index.html.
        
        // Временный токен, который будет заменен реальным токеном ID в index.html
        authToken = 'TEMP_TOKEN_WAITING_FOR_FIREBASE_AUTH'; 

        // 4. Загрузка состояния игры
        loadGameState();
        
        // 5. Цикл обновления:
        // Используем периодический опрос /load_state для обновления доступного дохода.
        setInterval(loadGameState, 5000); 
    }
    
    // ВАЖНО: Мы перенесли аутентификацию в index.html (внутри <script type="module">), 
    // поэтому main() будет запущена там после получения токена.
    // Здесь мы просто определяем функции и DOM-логику.
});

// Глобальная функция, вызываемая из index.html после успешной аутентификации.
window.initAppAfterAuth = (firebaseIdToken) => {
    authToken = firebaseIdToken;
    // После получения токена ID запускаем загрузку состояния
    loadGameState(); 

    // Запускаем цикл обновления только после первой успешной загрузки состояния
    setInterval(loadGameState, 5000); 
}
