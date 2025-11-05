// --- КОНСТАНТЫ И ГЛОБАЛЬНОЕ СОСТОЯНИЕ ---

// В Render API URL будет относительным, так как фронтенд и бэкенд на одном домене
const API_BASE = window.location.origin; 
const BUY_ENDPOINT = `${API_BASE}/api/buy_sector`;
const LOAD_ENDPOINT = `${API_BASE}/api/load_state`;
const COLLECT_ENDPOINT = `${API_BASE}/api/collect_income`;

let gameState = {
    balance: 0.00,
    sectors: {},
    last_collection_time: new Date().toISOString()
};

const SECTOR_METADATA = [
    { id: "sector1", name: "Зона отдыха", desc: "Парки и скверы для жителей.", icon: "🌳", base_rate: 0.5 },
    { id: "sector2", name: "Бизнес-центр", desc: "Коммерческие площади и коворкинги.", icon: "🏢", base_rate: 2.0 },
    { id: "sector3", name: "Индустриальная зона", desc: "Крупные заводы и склады.", icon: "🏭", base_rate: 10.0 },
];

const COST_MULTIPLIER = 1.15;

// --- УТИЛИТЫ ---

/**
 * Форматирует число до двух знаков после запятой.
 * @param {number} value 
 * @returns {string}
 */
const formatNumber = (value) => {
    return (Math.round(value * 100) / 100).toFixed(2);
};

/**
 * Рассчитывает стоимость следующего уровня сектора.
 * @param {string} sectorId
 * @param {number} currentLevel
 * @returns {number}
 */
const calculateCost = (sectorId, currentLevel) => {
    const baseCost = sectorId === "sector1" ? 100.0 : sectorId === "sector2" ? 500.0 : 2500.0;
    return Math.round(baseCost * (COST_MULTIPLIER ** currentLevel));
};


// --- ВЗАИМОДЕЙСТВИЕ С API ---

/**
 * Выполняет POST-запрос к API с обработкой ошибок.
 * @param {string} url - URL конечной точки API.
 * @param {object | null} body - Тело запроса (JSON).
 * @returns {Promise<object | null>} - Объект ответа с данными или null в случае ошибки.
 */
async function fetchApi(url, body = null) {
    try {
        document.getElementById('backend-status').textContent = 'Отправка запроса...';
        const options = {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
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
            // Обработка бизнес-логики ошибок (например, "Недостаточно средств")
            console.error("API Error:", data.detail || 'Неизвестная ошибка API', response.status);
            document.getElementById('backend-status').textContent = `Ошибка: ${data.detail || 'API Error'}`;
            // Показываем ошибку пользователю
            showNotification('Ошибка!', data.detail || 'Ошибка связи с сервером.', 'error');
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
        Object.assign(gameState, data.state);
        updateUI();
        // Запускаем таймер сбора дохода только после успешной загрузки
        startIncomeTimer(); 
    }
}

/**
 * Отправляет запрос на покупку сектора.
 * @param {string} sectorId 
 */
async function buySector(sectorId) {
    const data = await fetchApi(BUY_ENDPOINT, { sector: sectorId });
    if (data && data.state) {
        Object.assign(gameState, data.state);
        updateUI();
        // Отправляем обратную связь в Telegram, что покупка прошла успешно
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
        
        // Показываем сообщение о собранном доходе
        const collectedMsg = document.getElementById('collected-message');
        collectedMsg.textContent = `Доход собран! +${formatNumber(data.collected)} BSS`;
        collectedMsg.classList.remove('hidden');
        setTimeout(() => collectedMsg.classList.add('hidden'), 3000);
        
        updateUI();
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
    const now = new Date();
    const lastTime = new Date(gameState.last_collection_time);
    const timeDeltaSeconds = (now.getTime() - lastTime.getTime()) / 1000;
    const collectedAmount = totalIncome * timeDeltaSeconds;
    
    document.getElementById('collect-amount').textContent = formatNumber(collectedAmount);
    
    const collectButton = document.getElementById('collect-button');
    if (collectedAmount > 0.01) {
        collectButton.disabled = false;
        collectButton.textContent = `Собрать доход (${formatNumber(collectedAmount)} BSS)`;
    } else {
        collectButton.disabled = true;
        collectButton.textContent = `Собрать доход (0.00 BSS)`;
    }
    
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
                    <p class="text-sm text-green-400 mt-1">Доход: ${formatNumber(meta.base_rate)} BSS/сек</p>
                </div>
            </div>
            <button 
                id="buy-${meta.id}" 
                data-sector-id="${meta.id}"
                class="buy-button py-2 px-4 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-lg transition duration-150 ease-in-out disabled:bg-gray-500 disabled:text-gray-300 disabled:cursor-not-allowed text-sm w-28"
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
 * ВАЖНО: Это только визуальный эффект. Фактический баланс всегда берется с сервера.
 */
function startIncomeTimer() {
    const totalIncomeRate = SECTOR_METADATA.reduce((sum, meta) => {
        const level = gameState.sectors[meta.id] || 0;
        return sum + meta.base_rate * level;
    }, 0);
    
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
        
        // Показываем toast-уведомление для лучшей обратной связи
        if (webApp.HapticFeedback) {
            if (type === 'success') {
                webApp.HapticFeedback.notificationOccurred('success');
            } else if (type === 'error') {
                webApp.HapticFeedback.notificationOccurred('error');
            }
        }
    } else {
        // Fallback для старых версий
        console.warn(`[${type}] ${title}: ${text}`);
    }
}


/**
 * Инициализация WebApp.
 */
function initWebApp() {
    if (window.Telegram && window.Telegram.WebApp) {
        const webApp = window.Telegram.WebApp;
        webApp.ready();
        
        // Устанавливаем цвет темы (если применимо)
        if (webApp.themeParams) {
             // Используем цвет фона из темы Telegram
            document.body.style.backgroundColor = webApp.themeParams.bg_color || '#1a1a1a'; 
        }

        // Показываем главную кнопку, если она нужна (например, для закрытия)
        // webApp.MainButton.setText("Закрыть");
        // webApp.MainButton.onClick(() => webApp.close());
        // webApp.MainButton.show();
        
        document.getElementById('tg-status').textContent = 'Готово';
        
        // 1. Загрузка состояния после инициализации WebApp
        loadGameState();

        // 2. Установка слушателя на кнопку сбора
        document.getElementById('collect-button').addEventListener('click', collectIncome);

    } else {
        // Заглушка для отладки вне Telegram
        document.getElementById('tg-status').textContent = 'Режим отладки (вне TG)';
        loadGameState(); 
        document.getElementById('collect-button').addEventListener('click', collectIncome);
    }
}


// --- ЗАПУСК ---
window.onload = initWebApp;
