// Убедимся, что Telegram WebApp SDK загружен
if (typeof Telegram !== 'undefined' && Telegram.WebApp) {
    const tg = Telegram.WebApp;
    tg.ready();
    tg.expand(); // Расширяем WebApp на весь экран

    // Замените этот URL на фактический адрес вашего API (например, на Render)
    const API_BASE_URL = window.location.origin; 
    let USER_ID = null;
    let playerState = {};
    let lastRenderTime = 0;
    const UPDATE_INTERVAL = 1000; // Интервал обновления UI в миллисекундах

    // ----------------------------------------------------
    // Утилиты
    // ----------------------------------------------------

    // Функция для форматирования числа с пробелами
    const formatBSS = (num) => Math.floor(num).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");

    // ----------------------------------------------------
    // Отрисовка UI
    // ----------------------------------------------------

    const renderSectorCard = (id, data, config) => {
        const isOwned = !!playerState.sectors[id];
        // Добавляем проверку, чтобы нельзя было собрать меньше 1 BSS
        const isCollectable = isOwned && data.income_to_collect >= 1; 
        const incomeDisplay = isOwned 
            ? `${formatBSS(data.income_per_second)} BSS/сек`
            : `Доход: ${formatBSS(config.base_income)} BSS/сек`;
        
        const accumulatedDisplay = isOwned 
            ? `Накоплено: ${formatBSS(data.income_to_collect)} BSS`
            : `Цена: ${formatBSS(config.base_cost)} BSS`;

        const buttonText = isOwned ? "Собрать доход" : "Купить";
        
        // Определяем, активна ли кнопка сбора
        const isCollectButtonActive = isOwned && isCollectable;
        // Определяем, активна ли кнопка покупки
        const isBuyButtonActive = !isOwned && playerState.balance >= config.base_cost;

        // Определяем класс кнопки
        let buttonClass = '';
        let isDisabled = false;

        if (isOwned) {
            buttonClass = isCollectButtonActive ? 'bg-green-600 hover:bg-green-700' : 'bg-gray-400 cursor-not-allowed';
            isDisabled = !isCollectButtonActive;
        } else {
            buttonClass = isBuyButtonActive ? 'bg-indigo-600 hover:bg-indigo-700' : 'bg-red-400 cursor-not-allowed';
            isDisabled = !isBuyButtonActive;
        }


        const buttonAction = isOwned 
            ? `collectIncome('${id}')`
            : `buySector('${id}')`;

        // Расчет прогресса для прогресс-бара
        // Максимальное значение для 100% поставим, например, 60 секунд * доход (чтобы бар не был слишком длинным)
        const progressMax = data.income_per_second * 60; 
        const progressValue = Math.min(data.income_to_collect, progressMax);
        const progressPercent = (progressValue / progressMax) * 100;

        const progressBar = isOwned ? `
            <div class="h-2 bg-gray-200 rounded-full mt-2 overflow-hidden">
                <div class="h-full bg-green-500 transition-all duration-300" style="width: ${progressPercent}%;"></div>
            </div>
            <p class="text-xs text-gray-500 mt-1">${progressPercent.toFixed(0)}% до 1 минуты дохода</p>
        ` : '';

        return `
            <div class="bg-white p-4 rounded-xl shadow-md flex items-center justify-between transition-transform duration-200 hover:scale-[1.01] ${isOwned ? 'border-l-4 border-green-500' : 'border-l-4 border-indigo-500'}">
                <div class="flex-grow">
                    <p class="text-lg font-bold ${isOwned ? 'text-green-700' : 'text-indigo-700'}">${config.name}</p>
                    <p class="text-sm text-gray-600 mt-1">${incomeDisplay}</p>
                    <p class="text-base font-semibold mt-1">${accumulatedDisplay}</p>
                    ${progressBar}
                </div>
                <button 
                    onclick="${buttonAction}" 
                    ${isDisabled ? 'disabled' : ''}
                    class="ml-4 px-4 py-2 text-sm font-semibold text-white rounded-lg ${buttonClass} transition duration-150 shadow-md focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
                >
                    ${buttonText}
                </button>
            </div>
        `;
    };

    // Функция для обновления всего интерфейса
    const renderUI = () => {
        if (typeof playerState.balance === 'undefined') return;

        // 1. Обновляем баланс в шапке
        document.getElementById('balance-display').innerText = formatBSS(playerState.balance) + ' BSS';
        
        // 2. Обновляем общий накопленный доход
        document.getElementById('total-income-display').innerText = 
            `Общий накопленный доход: ${formatBSS(playerState.total_accumulated_income || 0)} BSS`;

        // 3. Отрисовываем сектора и магазин
        const sectorsContainer = document.getElementById('sectors-container');
        const shopContainer = document.getElementById('shop-container');
        sectorsContainer.innerHTML = '';
        shopContainer.innerHTML = '';

        const ownedSectors = [];
        const availableSectors = [];

        // Проходим по всем доступным индустриям из конфигурации
        const industriesConfig = playerState.industries_config || {};
        const sectors = playerState.sectors || {};

        for (const id in industriesConfig) {
            const config = industriesConfig[id];
            const sectorData = sectors[id];

            if (sectorData) {
                ownedSectors.push({ id, data: sectorData, config });
            } else {
                availableSectors.push({ id, data: {}, config });
            }
        }

        // Рендеринг купленных секторов
        if (ownedSectors.length > 0) {
            ownedSectors.forEach(item => {
                sectorsContainer.innerHTML += renderSectorCard(item.id, item.data, item.config);
            });
        } else {
             sectorsContainer.innerHTML = `
                <div class="text-center p-6 bg-white rounded-xl shadow-md text-gray-500">
                    У вас пока нет активных секторов. Купите первый в магазине!
                </div>
            `;
        }

        // Рендеринг магазина
        if (availableSectors.length > 0) {
            availableSectors.forEach(item => {
                shopContainer.innerHTML += renderSectorCard(item.id, item.data, item.config);
            });
        } else {
             shopContainer.innerHTML = `
                <div class="text-center p-6 bg-white rounded-xl shadow-md text-gray-500">
                    Все доступные секторы куплены! Ожидайте обновлений.
                </div>
            `;
        }
        
        // 4. Обновляем главную кнопку Telegram
        tg.MainButton.setText(`Баланс: ${formatBSS(playerState.balance)} BSS`);
        tg.MainButton.show();
    };
    
    // --- Обновление накопленного дохода в реальном времени (клиентский расчет) ---
    const updateAccumulatedIncome = () => {
        const currentTime = Math.floor(Date.now() / 1000);
        let total_accumulated = 0;

        for (const sectorId in playerState.sectors) {
            const sector = playerState.sectors[sectorId];
            const lastCollect = sector.last_collect_time || currentTime;
            const incomePerSecond = sector.income_per_second || 0;
            
            const timeElapsed = currentTime - lastCollect;
            // Используем Math.floor, так как доход бэкенд тоже округляет до целого
            const accumulated = Math.floor(timeElapsed * incomePerSecond); 
            
            sector.income_to_collect = accumulated;
            total_accumulated += accumulated;
        }

        playerState.total_accumulated_income = total_accumulated;
        
        // Перерисовываем только секторы и баланс
        // Проверяем наличие элемента перед вызовом renderUI
        if (document.getElementById('sectors-container')) {
            renderUI();
        }
    };
    
    setInterval(updateAccumulatedIncome, UPDATE_INTERVAL); // Обновляем UI каждую секунду

    // ----------------------------------------------------
    // Взаимодействие с API
    // ----------------------------------------------------

    /**
     * Загружает данные игрока и рассчитывает накопленную прибыль.
     */
    const loadPlayerState = async () => {
        const loadingIndicator = document.getElementById('loading-indicator');
        if (loadingIndicator) loadingIndicator.innerText = "Загрузка данных секторов...";
        
        try {
            const url = `${API_BASE_URL}/api/load_state?user_id=${USER_ID}`;
            const response = await fetch(url);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            // Если возникла ошибка (например, Database not initialized)
            if (data.error) {
                 tg.showAlert(`Ошибка загрузки: ${data.error}`);
                 return;
            }

            playerState = data;
            console.log("Данные игрока загружены:", playerState);
            renderUI();
        } catch (error) {
            console.error("Error loading player state:", error);
            tg.showAlert(`Не удалось загрузить игру: ${error.message}`);
        }
    };

    /**
     * Отправляет запрос на сбор дохода для конкретного сектора.
     * @param {string} sectorId - ID сектора, откуда собираем (например, "1")
     */
    window.collectIncome = async (sectorId) => {
        if (!USER_ID) return;

        tg.MainButton.showProgress(true);

        try {
            const response = await fetch(`${API_BASE_URL}/api/collect_income`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: USER_ID, sector_id: sectorId })
            });

            const result = await response.json();
            
            if (response.ok && result.success) {
                // После успешного сбора, немедленно перезагружаем состояние с бэкенда.
                await loadPlayerState(); 
                
                tg.showPopup({ message: `Собрано: ${formatBSS(result.collected)} BSS!`, title: "Сбор дохода", type: "success" });
            } else {
                 // Обрабатываем ошибки, включая HTTPException с сервера
                 tg.showAlert(`Сбор не удался: ${result.detail || result.message || 'Ещё не готово или произошла ошибка.'}`);
            }

        } catch (error) {
            console.error("Error collecting income:", error);
            tg.showAlert('Ошибка связи с сервером.');
        } finally {
            tg.MainButton.hideProgress();
        }
    };
    
    /**
     * Отправляет запрос на покупку нового сектора.
     * @param {string} sectorId - ID сектора для покупки (например, "2")
     */
    window.buySector = async (sectorId) => {
        if (!USER_ID) return;

        tg.MainButton.showProgress(true);

        try {
            const response = await fetch(`${API_BASE_URL}/api/buy_sector`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: USER_ID, sector_id: sectorId })
            });

            const result = await response.json();

            if (response.ok && result.success) {
                // После успешной покупки перезагружаем состояние, чтобы обновить баланс и список секторов
                await loadPlayerState(); 
                
                tg.showPopup({ message: `Вы купили ${result.sector_name} за ${formatBSS(result.cost)} BSS!`, title: "Покупка успешна", type: "success" });
            } else {
                 // Обработка ошибок (Недостаточно средств/уже куплен/HTTPException)
                 tg.showAlert(`Покупка не удалась: ${result.detail || result.message || 'Ошибка.'}`);
            }
        } catch (error) {
            console.error("Error buying sector:", error);
            tg.showAlert('Ошибка связи с сервером при покупке.');
        } finally {
            tg.MainButton.hideProgress();
        }
    };


    // ----------------------------------------------------
    // ИНИЦИАЛИЗАЦИЯ
    // ----------------------------------------------------
    const initApp = () => {
        // Устанавливаем ID пользователя из Telegram
        if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
            USER_ID = tg.initDataUnsafe.user.id;
            // Убедимся, что ID пользователя - это число (как ожидается бэкендом)
            if (typeof USER_ID === 'string') {
                USER_ID = parseInt(USER_ID, 10);
            }
            loadPlayerState();
        } else {
            // Режим отладки, если не в Telegram
            USER_ID = 123456789; 
            console.warn("Запуск вне Telegram. Используется DEBUG_USER_123456789.");
            loadPlayerState();
        }
    };

    // Запускаем инициализацию после загрузки WebApp SDK
    initApp();

} else {
    // В случае, если WebApp SDK не загружен
    document.body.innerHTML = '<div style="text-align: center; padding: 20px; color: red;">Ошибка: Запустите игру в браузере Telegram.</div>';
    console.error("Telegram WebApp SDK не загружен.");
}
