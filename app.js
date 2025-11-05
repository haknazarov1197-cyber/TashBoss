// --- КОНСТАНТЫ И ГЛОБАЛЬНОЕ СОСТОЯНИЕ ---

// КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Используем window.location.origin для относительного пути API
const API_BASE = window.location.origin; 
const BUY_ENDPOINT = `${API_BASE}/api/buy_sector`;
const LOAD_ENDPOINT = `${API_BASE}/api/load_state`;
const COLLECT_ENDPOINT = `${API_BASE}/api/collect_income`;

// Глобальное состояние игры
let gameState = {
    balance: 0.00,
    sectors: {},
    last_collection_time: new Date().toISOString()
};

// Метаданные секторов
const SECTOR_METADATA = [
    { id: "sector1", name: "Зона отдыха", desc: "Парки и скверы для жителей.", icon: "🌳", base_rate: 0.5, base_cost: 100.0 },
    { id: "sector2", name: "Бизнес-центр", desc: "Коммерческие площади и коворкинги.", icon: "🏢", base_rate: 2.0, base_cost: 500.0 },
    { id: "sector3", name: "Индустриальная зона", desc: "Крупные заводы и склады.", icon: "🏭", base_rate: 10.0, base_cost: 2500.0 },
];

const COST_MULTIPLIER = 1.15;
// Глобальный токен для аутентификации в API
window.__firebase_id_token = ''; 


// --- УТИЛИТЫ И ЛОГИКА ---

/**
 * Возвращает Firebase ID Token из глобальной переменной.
 * @returns {string}
 */
const getAuthToken = () => {
    return window.__firebase_id_token || ''; 
};

/**
 * Форматирует число до двух знаков после запятой.
 * @param {number} value 
 * @returns {string}
 */
const formatNumber = (value) => {
    // Используем Math.floor для более "реалистичного" отображения накопления, но округляем для баланса
    return (Math.round(value * 100) / 100).toFixed(2);
};

/**
 * Рассчитывает стоимость следующего уровня сектора.
 * @param {string} sectorId
 * @param {number} currentLevel
 * @returns {number}
 */
const calculateCost = (sectorId, currentLevel) => {
    const baseCost = SECTOR_METADATA.find(m => m.id === sectorId)?.base_cost || 100;
    // Используем Math.round, чтобы стоимость всегда была целым числом
    return Math.round(baseCost * (COST_MULTIPLIER ** currentLevel));
};

/**
 * Рассчитывает текущий накопленный доход без обращения к API.
 * @param {object} state - Текущее состояние игры
 * @returns {number}
 */
const getUncollectedIncome = (state) => {
    const totalIncomeRate = SECTOR_METADATA.reduce((sum, meta) => {
        const level = state.sectors[meta.id] || 0;
        return sum + meta.base_rate * level;
    }, 0);
    
    const now = new Date();
    const lastTime = new Date(state.last_collection_time);
    const timeDeltaSeconds = (now.getTime() - lastTime.getTime()) / 1000;
    
    return totalIncomeRate * timeDeltaSeconds;
}


// --- ВЗАИМОДЕЙСТВИЕ С API ---

/**
 * Выполняет POST-запрос к API с обработкой ошибок.
 * @param {string} url - URL конечной точки API.
 * @param {object | null} body - Тело запроса (JSON).
 * @returns {Promise<object | null>} - Объект ответа с данными или null в случае ошибки.
 */
async function fetchApi(url, body = null) {
    const authToken = getAuthToken(); 
    
    if (!authToken) {
         document.getElementById('backend-status').textContent = 'Ошибка: Нет токена Firebase';
         showNotification('Ошибка аутентификации', 'Не удалось получить токен авторизации.', 'error');
         return null;
    }
    
    try {
        document.getElementById('backend-status').textContent = 'Отправка запроса...';
        const options = {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // Передача Firebase ID Token
                'Authorization': `Bearer ${authToken}`
            }
        };

        if (body) {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(url, options);
        const data = await response.json();

        if (response.ok && data.status === 'ok') {
            document.getElementById('backend-status').textContent = 'OK';
            return data;
        } else {
            // Обработка ошибок, возвращенных API (напр. 401, 400, 500)
            const detail = data.detail || 'Неизвестная ошибка API';
            console.error("API Error:", detail, response.status);
            document.getElementById('backend-status').textContent = `Ошибка: ${response.status} (${detail})`;
            showNotification('Ошибка!', detail, 'error');
            return null;
        }

    } catch (error) {
        console.error("Fetch failed:", error);
        document.getElementById('backend-status').textContent = 'Сбой связи с сервером!';
        showNotification('Критическая ошибка', 'Сбой связи с сервером. Проверьте подключение.', 'error');
        return null;
    }
}

/**
 * Загружает начальное состояние игры с сервера.
 */
async function loadGameState() {
    const data = await fetchApi(LOAD_ENDPOINT);
    if (data && data.state) {
        // Обновляем глобальное состояние
        Object.assign(gameState, data.state);
        updateUI();
        startIncomeTimer(); 
    }
}

/**
 * Отправляет запрос на покупку сектора.
 * @param {string} sectorId 
 */
async function buySector(sectorId) {
    // Временно отключаем все кнопки покупки для предотвращения двойного клика
    document.querySelectorAll('.buy-button').forEach(btn => btn.disabled = true);
    
    const data = await fetchApi(BUY_ENDPOINT, { sector: sectorId });
    
    // Включаем кнопки после завершения запроса (внутри updateUI они будут включены/выключены по логике)
    document.querySelectorAll('.buy-button').forEach(btn => btn.disabled = false);
    
    if (data && data.state) {
        Object.assign(gameState, data.state);
        updateUI();
        const sectorName = SECTOR_METADATA.find(s => s.id === sectorId)?.name || sectorId;
        showNotification('Покупка успешна!', `${sectorName} улучшен до ур. ${gameState.sectors[sectorId]}.`, 'success');
    }
}

/**
 * Отправляет запрос на сбор пассивного дохода.
 */
async function collectIncome() {
    const button = document.getElementById('collect-button');
    button.disabled = true;
    
    const data = await fetchApi(COLLECT_ENDPOINT);
    
    if (data && data.state) {
        Object.assign(gameState, data.state);
        
        const collectedMsg = document.getElementById('collected-message');
        collectedMsg.textContent = `Доход собран! +${formatNumber(data.collected)} BSS`;
        collectedMsg.classList.remove('hidden');
        button.disabled = false;
        
        setTimeout(() => {
             collectedMsg.classList.add('hidden');
        }, 3000);
        
        updateUI();
    } else {
         button.disabled = false;
    }
}

// --- УПРАВЛЕНИЕ UI И РЕНДЕРИНГ ---

/**
 * Обновляет все элементы UI, основываясь на gameState.
 */
function updateUI() {
    // 1. Обновление баланса и дохода
    const totalIncome = SECTOR_METADATA.reduce((sum, meta) => {
        const level = gameState.sectors[meta.id] || 0;
        return sum + meta.base_rate * level;
    }, 0);
    
    document.getElementById('balance-display').textContent = formatNumber(gameState.balance);
    document.getElementById('income-rate-display').textContent = formatNumber(totalIncome);

    // 2. Обновление кнопки сбора
    const collectedAmount = getUncollectedIncome(gameState);
    
    const collectAmountSpan = document.getElementById('collect-amount');
    collectAmountSpan.textContent = formatNumber(collectedAmount);
    
    const collectButton = document.getElementById('collect-button');
    // Включаем кнопку, если есть что собирать
    collectButton.disabled = collectedAmount < 0.01;
    collectButton.textContent = `Собрать доход (${formatNumber(collectedAmount)} BSS)`;
    
    // 3. Перерисовка списка секторов
    renderSectors();
}

/**
 * Рисует список секторов.
 */
function renderSectors() {
    const container = document.getElementById('sectors-container');
    container.innerHTML = ''; // Очищаем контейнер

    SECTOR_METADATA.forEach(meta => {
        const currentLevel = gameState.sectors[meta.id] || 0;
        const nextCost = calculateCost(meta.id, currentLevel);
        const canAfford = gameState.balance >= nextCost;

        const card = document.createElement('div');
        card.className = 'bg-gray-700 p-4 rounded-xl flex justify-between items-center card-shadow';
        card.innerHTML = `
            <div class="flex items-start">
                <span class="text-3xl mr-3">${meta.icon}</span>
                <div>
                    <h3 class="text-lg font-semibold text-white">${meta.name} (Ур. ${currentLevel})</h3>
                    <p class="text-xs text-gray-400">${meta.desc}</p>
                    <p class="text-sm text-green-400 mt-1">Доход: ${formatNumber(meta.base_rate * (currentLevel + 1))} BSS/сек</p>
                </div>
            </div>
            <button 
                id="buy-${meta.id}" 
                data-sector-id="${meta.id}"
                class="buy-button py-2 px-4 font-bold rounded-lg transition duration-150 ease-in-out disabled:bg-gray-500 disabled:text-gray-300 disabled:cursor-not-allowed text-sm w-28"
                ${canAfford ? '' : 'disabled'}>
                Купить за ${formatNumber(nextCost)}
            </button>
        `;
        container.appendChild(card);
    });

    // 4. Добавляем слушателей событий к кнопкам покупки
    document.querySelectorAll('.buy-button').forEach(button => {
        button.addEventListener('click', (e) => {
            const sectorId = e.target.dataset.sectorId;
            if (sectorId) {
                buySector(sectorId);
            }
        });
    });
}

/**
 * Имитирует прибавление пассивного дохода к балансу в UI каждую секунду.
 */
function startIncomeTimer() {
    // Обновляем UI каждую секунду, чтобы показать накопление дохода и обновить кнопку сбора.
    setInterval(updateUI, 1000);
}


// --- УПРАВЛЕНИЕ TELEGRAM WEB APP ---

/**
 * Показывает нативное всплывающее уведомление Telegram.
 * @param {string} title
 * @param {string} text
 * @param {'success'|'error'|'info'} type
 */
function showNotification(title, text, type) {
    if (window.Telegram && window.Telegram.WebApp.isVersionAtLeast('6.2')) {
        const webApp = window.Telegram.WebApp;
        webApp.showPopup({
            title: title,
            message: text,
            buttons: [{ id: 'ok', type: 'ok' }]
        });
        
        if (webApp.HapticFeedback) {
            if (type === 'success') {
                webApp.HapticFeedback.notificationOccurred('success');
            } else if (type === 'error') {
                webApp.HapticFeedback.notificationOccurred('error');
            }
        }
    } else {
        console.warn(`[${type}] ${title}: ${text}`);
    }
}


// --- ИНИЦИАЛИЗАЦИЯ FIREBASE И WEBAPP ---

// Подключаем Firebase SDK
import { initializeApp } from "https://www.gstatic.com/firebasejs/11.6.1/firebase-app.js";
import { getAuth, signInWithCustomToken } from "https://www.gstatic.com/firebasejs/11.6.1/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/11.6.1/firebase-firestore.js";

async function initWebApp() {
    document.getElementById('tg-status').textContent = 'Подключение...';
    
    // Глобальные переменные Canvas
    const firebaseConfig = JSON.parse(window.__firebase_config || '{}');
    const initialAuthToken = window.__initial_auth_token;

    if (!firebaseConfig || !initialAuthToken) {
        document.getElementById('tg-status').textContent = 'Ошибка: Нет конфига/токена Firebase';
        console.error("Firebase config or auth token is missing. Cannot proceed.");
        showNotification('Ошибка', 'Отсутствуют данные для аутентификации.', 'error');
        return;
    }

    try {
        // 1. Инициализация Firebase
        const app = initializeApp(firebaseConfig);
        const auth = getAuth(app);
        getFirestore(app); // Инициализируем Firestore
        
        // 2. Аутентификация с помощью Custom Token
        const userCredential = await signInWithCustomToken(auth, initialAuthToken);
        const user = userCredential.user;

        // 3. Получение Firebase ID Token для API-запросов
        const idToken = await user.getIdToken();
        window.__firebase_id_token = idToken; 
        
        // 4. Настройка Telegram WebApp
        if (window.Telegram && window.Telegram.WebApp) {
            const webApp = window.Telegram.WebApp;
            webApp.ready();
            // Настраиваем тему, если доступно
            if (webApp.themeParams) {
                document.body.style.backgroundColor = webApp.themeParams.bg_color || '#1a1a1a';
                // Обновляем CSS-переменные для кнопок
                document.documentElement.style.setProperty('--tg-theme-button-color', webApp.themeParams.button_color || '#4CAF50');
                document.documentElement.style.setProperty('--tg-theme-button-text-color', webApp.themeParams.button_text_color || '#ffffff');
            }
        }
        
        document.getElementById('tg-status').textContent = `Готово (User: ${user.uid.substring(0, 8)}...)`;
        
        // 5. Загрузка состояния игры и запуск логики
        document.getElementById('collect-button').addEventListener('click', collectIncome);
        loadGameState();

    } catch (error) {
        console.error("Authentication or Initialization failed:", error);
        document.getElementById('tg-status').textContent = 'Ошибка аутентификации';
        showNotification('Ошибка аутентификации', 'Не удалось войти в систему. Попробуйте перезапустить Mini App.', 'error');
    }
}


// --- ЗАПУСК ---
window.onload = initWebApp;
