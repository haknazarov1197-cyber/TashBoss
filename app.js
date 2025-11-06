// Глобальные переменные Firebase, как в HTML-файле
let app;
let db;
let auth;
let userId = null;
let isAuthReady = false; // Флаг, указывающий, что аутентификация завершена

// Глобальные переменные игры
let gameState = null;
let passiveIncomeInterval = null;
const API_BASE_URL = window.location.origin;

// --- Утилиты Firebase ---

// Обработчик ошибки Firebase
const handleAuthError = (message, error) => {
    console.error(message, error);
    const appContainer = document.getElementById('app-container');
    appContainer.innerHTML = `
        <div class="p-6 bg-blue-900 rounded-xl shadow-2xl max-w-sm mx-auto mt-20 text-center">
            <h1 class="text-xl font-bold text-white mb-4">TashBoss Clicker</h1>
            <p class="text-yellow-400">Ошибка: Не удалось войти в Firebase. Пожалуйста, проверьте консоль.</p>
        </div>
    `;
};

// Функция для получения токена Firebase ID с бэкенда
async function getFirebaseToken(initData) {
    const url = `${API_BASE_URL}/api/get_firebase_token`;
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ init_data: initData }),
        });

        if (!response.ok) {
            // Если Webhook API возвращает 404, это означает, что токен не получен
            const errorData = await response.json();
            throw new Error(`Ошибка HTTP: ${response.status}. Детали: ${errorData.detail || 'Неизвестно'}`);
        }

        const data = await response.json();
        return data.firebase_token;
    } catch (error) {
        console.error("Ошибка при получении кастомного токена Firebase с бэкенда:", error);
        throw new Error("Не удалось получить токен. Ошибка HTTP: 404. Not Found"); // Оставим эту ошибку для наглядности
    }
}


// Основная функция инициализации аутентификации
async function initAuth() {
    const firebaseConfigElement = document.getElementById('firebase-config');
    const firebaseTokenElement = document.getElementById('initial-auth-token');

    if (!firebaseConfigElement) {
        return handleAuthError("Элемент firebase-config не найден.", null);
    }
    const firebaseConfig = JSON.parse(firebaseConfigElement.textContent);
    
    // Инициализация Firebase
    app = firebase.initializeApp(firebaseConfig);
    auth = firebase.auth(app);
    db = firebase.firestore(app);
    
    firebase.firestore.setLogLevel('debug'); // Для отладки
    
    // 1. Получение initData (ключевой момент для WebApp)
    const urlParams = new URLSearchParams(window.location.search);
    const initData = urlParams.get('tgWebAppData');
    
    let customToken = null;
    
    if (initData) {
        // Если WebApp открыт Telegram, используем initData для получения токена
        try {
            customToken = await getFirebaseToken(initData);
        } catch (error) {
            handleAuthError("Ошибка: Не удалось получить токен. Ошибка HTTP: 404. Not Found", error);
            isAuthReady = true;
            return;
        }
    } else if (firebaseTokenElement) {
        // Если открыто в Canvas (для тестирования), используем __initial_auth_token
        customToken = firebaseTokenElement.textContent;
    }

    // 2. Аутентификация
    auth.onAuthStateChanged(async (user) => {
        if (user) {
            userId = user.uid;
            isAuthReady = true;
            await loadGameState();
        } else if (customToken) {
            // Если есть токен, но пользователь не вошел, пытаемся войти
            try {
                await auth.signInWithCustomToken(customToken);
            } catch (error) {
                handleAuthError("Ошибка при входе с кастомным токеном.", error);
                isAuthReady = true;
            }
        } else {
            // Если токена нет (и не Telegram), входим анонимно (только для Canvas)
            try {
                await auth.signInAnonymously();
            } catch(error) {
                handleAuthError("Ошибка анонимного входа.", error);
                isAuthReady = true;
            }
        }
    });
}

// --- Утилиты API ---

// ... (Функции fetchWithAuth, loadGameState, collectIncome, buySector остаются БЕЗ ИЗМЕНЕНИЙ)

async function fetchWithAuth(endpoint, options = {}) {
    if (!userId) {
        throw new Error("Пользователь не авторизован.");
    }
    const token = await auth.currentUser.getIdToken();
    const headers = {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
        ...(options.headers || {})
    };
    
    const url = `${API_BASE_URL}${endpoint}`;
    
    try {
        const response = await fetch(url, { ...options, headers });
        if (response.status === 401) {
             // 401: Токен устарел или недействителен.
             await auth.signOut();
             alert("Сессия истекла. Пожалуйста, перезагрузите приложение.");
             throw new Error("Unauthorized");
        }
        if (!response.ok) {
            let errorDetail = "Неизвестная ошибка";
            try {
                const errorJson = await response.json();
                errorDetail = errorJson.detail || JSON.stringify(errorJson);
            } catch {}
            throw new Error(`Ошибка API: ${response.status} - ${errorDetail}`);
        }
        return response.json();
    } catch (error) {
        console.error(`Ошибка при обращении к ${endpoint}:`, error);
        throw error;
    }
}

async function loadGameState() {
    try {
        const data = await fetchWithAuth('/api/load_state', { method: 'POST' });
        gameState = data;
        renderGame();
        // Настройка интервала пассивного дохода
        if (passiveIncomeInterval) clearInterval(passiveIncomeInterval);
        passiveIncomeInterval = setInterval(renderGame, 1000); // Обновляем рендер каждую секунду
    } catch (error) {
        console.error("Не удалось загрузить состояние игры:", error);
        // Обработка ошибки загрузки: показать сообщение пользователю
        renderError("Не удалось загрузить состояние игры. Попробуйте перезапустить приложение.");
    }
}

async function collectIncome() {
    try {
        const data = await fetchWithAuth('/api/collect_income', { method: 'POST' });
        gameState = data;
        renderGame();
        showNotification(`Собрано: ${gameState.collected_amount.toFixed(2)} BossCoin!`, 'success');
    } catch (error) {
        console.error("Не удалось собрать доход:", error);
        showNotification("Ошибка сбора дохода. Попробуйте еще раз.", 'error');
    }
}

async function buySector(sectorId) {
    try {
        const data = await fetchWithAuth('/api/buy_sector', {
            method: 'POST',
            body: JSON.stringify({ sector_id: sectorId })
        });
        gameState = data;
        renderGame();
        if (gameState.purchase_successful) {
            showNotification(`Куплен ${sectorId} (Уровень ${gameState.sectors[sectorId] || 1})!`, 'success');
        } else {
            showNotification("Недостаточно BossCoin для покупки.", 'warning');
        }
        if (gameState.collected_amount > 0) {
            showNotification(`Также собрано: ${gameState.collected_amount.toFixed(2)} BossCoin!`, 'info');
        }
    } catch (error) {
        console.error("Не удалось купить сектор:", error);
        showNotification("Ошибка покупки сектора. Попробуйте еще раз.", 'error');
    }
}

// --- Утилиты Рендеринга ---

// ... (Функции calculateDisplayIncome, getSectorConfig, renderGame, renderError, showNotification остаются БЕЗ ИЗМЕНЕНИЙ)

function calculateDisplayIncome() {
    if (!gameState) return 0.0;
    
    // Разница между текущим временем и last_collection_time (в секундах)
    const lastTime = gameState.last_collection_time ? new Date(gameState.last_collection_time._seconds * 1000) : new Date();
    const now = new Date();
    
    let timeSinceLast = (now.getTime() - lastTime.getTime()) / 1000;
    
    // Ограничение накопления 7 днями (как на бэкенде)
    const maxSeconds = 7 * 24 * 60 * 60;
    if (timeSinceLast > maxSeconds) {
        timeSinceLast = maxSeconds;
    }
    
    let totalIncomePerSecond = 0.0;
    const sectors = gameState.sectors || {};
    
    // Расчет общего дохода в секунду
    for (const [sectorId, level] of Object.entries(sectors)) {
        const config = getSectorConfig(sectorId);
        if (config && level > 0) {
            totalIncomePerSecond += config.passive_income * level;
        }
    }
    
    // Накопленный доход (доступный + доход за последние секунды)
    const accruedIncome = gameState.available_income + (totalIncomePerSecond * timeSinceLast);

    return accruedIncome > 0 ? accruedIncome : 0.0;
}

function getSectorConfig(sectorId) {
    // Эта конфигурация должна соответствовать бэкенду
    const configs = {
        "sector1": {"name": "Сектор 'А'", "passive_income": 0.5, "base_cost": 100.0},
        "sector2": {"name": "Сектор 'B'", "passive_income": 2.0, "base_cost": 500.0},
        "sector3": {"name": "Сектор 'C'", "passive_income": 10.0, "base_cost": 2500.0},
    };
    return configs[sectorId];
}

function renderGame() {
    const appContainer = document.getElementById('app-container');
    if (!gameState) {
        appContainer.innerHTML = '<div class="p-6 text-center text-white">Загрузка...</div>';
        return;
    }

    const currentBalance = gameState.balance || 0;
    const passiveIncomePerSecond = Object.entries(gameState.sectors || {}).reduce((sum, [id, level]) => {
        const config = getSectorConfig(id);
        return sum + (config ? config.passive_income * level : 0);
    }, 0);
    const availableIncome = calculateDisplayIncome();

    let sectorsHtml = '';
    const sectorIds = ['sector1', 'sector2', 'sector3'];

    sectorIds.forEach(sectorId => {
        const config = getSectorConfig(sectorId);
        const currentLevel = gameState.sectors[sectorId] || 0;
        const nextLevel = currentLevel + 1;
        const cost = config.base_cost * nextLevel;
        
        sectorsHtml += `
            <div class="bg-blue-800 p-4 rounded-xl shadow-lg flex justify-between items-center mb-4">
                <div>
                    <h3 class="text-lg font-bold text-white">${config.name}</h3>
                    <p class="text-sm text-gray-300">Уровень: ${currentLevel} (Доход: ${config.passive_income * currentLevel} / сек)</p>
                    <p class="text-xs text-gray-400 mt-1">Следующий ур. +${config.passive_income} / сек.</p>
                </div>
                <button 
                    onclick="buySector('${sectorId}')"
                    class="bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-4 rounded-lg transition duration-150 ease-in-out shadow-md disabled:bg-gray-500 disabled:cursor-not-allowed"
                    ${currentBalance < cost ? 'disabled' : ''}
                >
                    Купить (${cost.toFixed(2)} BC)
                </button>
            </div>
        `;
    });

    appContainer.innerHTML = `
        <div class="p-4 sm:p-6 md:p-8">
            <h1 class="text-3xl font-extrabold text-white text-center mb-6">TashBoss Clicker</h1>

            <div class="bg-gray-800 p-6 rounded-2xl shadow-xl mb-6">
                <p class="text-xl text-gray-300">Баланс BossCoin (BC):</p>
                <p class="text-4xl font-black text-yellow-400 mt-1">${currentBalance.toFixed(2)}</p>
            </div>

            <div class="bg-gray-800 p-6 rounded-2xl shadow-xl mb-6 flex justify-between items-center">
                <div>
                    <p class="text-lg text-gray-300">Накопленный доход:</p>
                    <p class="text-2xl font-bold text-green-400 mt-1">${availableIncome.toFixed(2)} BC</p>
                    <p class="text-sm text-gray-400 mt-2">Пассивный доход: ${passiveIncomePerSecond.toFixed(2)} BC / сек</p>
                </div>
                <button 
                    onclick="collectIncome()"
                    class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-3 px-6 rounded-xl transition duration-150 ease-in-out shadow-lg disabled:bg-gray-500 disabled:cursor-not-allowed"
                    ${availableIncome < 0.01 ? 'disabled' : ''}
                >
                    Собрать
                </button>
            </div>

            <h2 class="text-2xl font-bold text-white mb-4 border-b border-blue-700 pb-2">Развитие бизнеса</h2>
            ${sectorsHtml}
            
            <p class="text-center text-gray-500 mt-8 text-sm">Ваш User ID: <span class="break-all">${userId}</span></p>
        </div>
    `;
}

function renderError(message) {
    const appContainer = document.getElementById('app-container');
     appContainer.innerHTML = `
        <div class="p-6 bg-red-900 rounded-xl shadow-2xl max-w-sm mx-auto mt-20 text-center">
            <h1 class="text-xl font-bold text-white mb-4">TashBoss Clicker</h1>
            <p class="text-red-300">${message}</p>
        </div>
    `;
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    const color = type === 'success' ? 'bg-green-500' : type === 'warning' ? 'bg-yellow-500' : 'bg-blue-500';
    
    notification.className = `fixed bottom-4 left-1/2 transform -translate-x-1/2 p-3 ${color} text-white rounded-lg shadow-xl z-50 transition-opacity duration-300 opacity-0`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    // Показать и скрыть
    setTimeout(() => {
        notification.classList.remove('opacity-0');
        notification.classList.add('opacity-100');
    }, 10);
    
    setTimeout(() => {
        notification.classList.remove('opacity-100');
        notification.classList.add('opacity-0');
        notification.addEventListener('transitionend', () => notification.remove());
    }, 3000);
}


// Запуск инициализации при загрузке окна
window.onload = initAuth;
