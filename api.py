import React, { useState, useEffect, useCallback } from 'react';
import { Loader, Zap, Gift, RefreshCw, AlertTriangle, ChevronUp } from 'lucide-react';

// --- Константы и конфигурация API ---
const BASE_API_URL = '/api'; // Базовый путь для запросов к FastAPI
const MOCK_USER_ID = 'telegram_user_123456'; // Заглушка, заменить на реальный ID пользователя Telegram
const UPGRADE_COST = 100;

// --- Вспомогательная функция для API ---

/**
 * Выполняет запрос к API с логикой экспоненциального отката для обработки ошибок.
 * @param {string} endpoint - Конечная точка API.
 * @param {string} method - HTTP-метод.
 * @param {object} body - Тело запроса (для POST/PUT).
 * @param {number} retries - Максимальное количество попыток.
 */
const apiFetchWithRetry = async (endpoint, method = 'GET', body = null, retries = 3) => {
  const url = `${BASE_API_URL}${endpoint}`;
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };

  if (body) {
    options.body = JSON.stringify(body);
  }

  for (let i = 0; i < retries; i++) {
    try {
      const response = await fetch(url, options);
      
      if (response.ok) {
        // Если 204 No Content, возвращаем пустой объект
        if (response.status === 204) return {};
        return await response.json();
      }
      
      // Обработка HTTP ошибок (например, 400, 404, 500)
      const errorData = await response.json();
      throw new Error(errorData.detail || `HTTP Error ${response.status}: ${response.statusText}`);

    } catch (error) {
      if (i === retries - 1) {
        // Если это последняя попытка, пробрасываем ошибку
        throw error;
      }
      // Экспоненциальный откат: 1, 2, 4 секунды
      const delay = Math.pow(2, i) * 1000;
      console.warn(`[API] Попытка ${i + 1} не удалась. Повтор через ${delay / 1000}с...`);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
};


// --- Главный компонент игры ---

const App = () => {
  const [playerState, setPlayerState] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tapAnimation, setTapAnimation] = useState(false);

  // 1. Загрузка начального состояния игрока
  const fetchPlayerState = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const state = await apiFetchWithRetry(`/state/${MOCK_USER_ID}`);
      setPlayerState(state);
    } catch (e) {
      console.error("Ошибка загрузки состояния игрока:", e.message);
      setError(`Не удалось загрузить состояние игрока: ${e.message}`);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPlayerState();
  }, [fetchPlayerState]);

  // 2. Обработка клика (Tap)
  const handleTap = useCallback(async () => {
    if (!playerState || isLoading) return;

    // Оптимистичное обновление UI
    const currentScore = playerState.score;
    const clicksPerTap = playerState.clicks_per_tap;
    setPlayerState(prev => ({
      ...prev,
      score: currentScore + clicksPerTap
    }));

    // Анимация клика
    setTapAnimation(true);
    setTimeout(() => setTapAnimation(false), 200);

    try {
      // Запрос к бэкенду для сохранения
      const response = await apiFetchWithRetry(`/tap/${MOCK_USER_ID}`, 'POST');
      
      // Обновление состояния на основе ответа бэкенда (для синхронизации)
      setPlayerState(prev => ({
        ...prev,
        score: response.new_score,
        clicks_per_tap: response.clicks_per_tap || clicksPerTap // На случай, если CPT не изменился
      }));
    } catch (e) {
      console.error("Ошибка при клике:", e.message);
      setError(`Ошибка сохранения клика: ${e.message}`);
      // Откатываем оптимистичное обновление в случае ошибки
      setPlayerState(prev => ({ ...prev, score: currentScore }));
    }
  }, [playerState, isLoading]);


  // 3. Обработка покупки улучшения
  const handleUpgrade = useCallback(async () => {
    if (!playerState || isLoading || playerState.score < UPGRADE_COST) return;

    // Оптимистичное обновление UI
    const currentScore = playerState.score;
    const currentCPT = playerState.clicks_per_tap;
    setPlayerState(prev => ({
        ...prev,
        score: currentScore - UPGRADE_COST,
        clicks_per_tap: currentCPT + 1
    }));
    setError(null);

    try {
      const response = await apiFetchWithRetry(`/upgrade/${MOCK_USER_ID}`, 'POST');
      // Обновление состояния на основе ответа бэкенда (для синхронизации)
      setPlayerState(prev => ({
        ...prev,
        score: response.new_score,
        clicks_per_tap: response.new_clicks_per_tap
      }));
    } catch (e) {
      console.error("Ошибка при покупке улучшения:", e.message);
      setError(`Ошибка улучшения: ${e.message}. Пожалуйста, обновите страницу.`);
      // Откатываем оптимистичное обновление в случае ошибки
      setPlayerState(prev => ({ 
        ...prev, 
        score: currentScore,
        clicks_per_tap: currentCPT
      }));
    }
  }, [playerState, isLoading]);

  // --- Элементы UI ---

  if (error) {
    return (
      <div className="p-8 max-w-lg mx-auto bg-red-100 border-l-4 border-red-500 rounded-lg shadow-xl mt-12">
        <h2 className="text-2xl font-bold text-red-800 flex items-center mb-4">
          <AlertTriangle className="h-6 w-6 mr-2" /> Критическая ошибка
        </h2>
        <p className="text-red-700 mb-4">{error}</p>
        <button 
          onClick={fetchPlayerState}
          className="bg-red-500 text-white py-2 px-4 rounded-lg flex items-center hover:bg-red-600 transition-colors"
        >
          <RefreshCw className="h-4 w-4 mr-2" /> Повторить попытку
        </button>
      </div>
    );
  }

  if (isLoading || !playerState) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-50">
        <Loader className="animate-spin h-10 w-10 text-indigo-600 mb-4" />
        <p className="text-xl font-medium text-gray-700">Загрузка состояния игрока...</p>
      </div>
    );
  }
  
  const canUpgrade = playerState.score >= UPGRADE_COST;

  return (
    <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-start p-4 font-sans text-white">
      <script src="https://cdn.tailwindcss.com"></script>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap');
        body { font-family: 'Inter', sans-serif; }

        .tap-animation {
          transition: transform 0.1s ease-out, box-shadow 0.1s ease-out;
          transform: scale(0.95);
          box-shadow: 0 0 10px rgba(255, 255, 255, 0.5), 0 0 20px rgba(79, 70, 229, 0.8);
        }

        .tap-icon-bounce {
            animation: bounce-in 0.2s;
        }

        @keyframes bounce-in {
            0% { opacity: 0; transform: translateY(20px) scale(0.5); }
            100% { opacity: 1; transform: translateY(0) scale(1); }
        }
      `}</style>

      {/* Заголовок и Информация */}
      <div className="w-full max-w-md text-center mb-6 pt-4">
        <h1 className="text-3xl font-bold text-indigo-400">Cosmic Clicker 🌌</h1>
        <p className="text-sm text-gray-400 mt-1">
          Пользователь: <span className="font-mono bg-gray-800 px-2 py-0.5 rounded text-indigo-300 text-xs">{MOCK_USER_ID}</span>
        </p>
      </div>

      {/* Секция Счетчика */}
      <div className="w-full max-w-md bg-gray-800 p-6 rounded-2xl shadow-2xl border border-gray-700 mb-8">
        <div className="flex flex-col items-center">
          <p className="text-gray-400 text-xl font-medium mb-1">Ваши Очки (Score):</p>
          <p className="text-7xl font-extrabold text-white tracking-tight leading-none transition-transform duration-100">
            {playerState.score.toLocaleString()}
          </p>
          <p className="text-lg font-medium text-green-400 mt-2 flex items-center">
            <Zap className="h-5 w-5 mr-1 text-yellow-400" />
            Кликов за тап: {playerState.clicks_per_tap}
          </p>
        </div>
      </div>

      {/* Кнопка Клика */}
      <div 
        onClick={handleTap}
        className={`
          w-48 h-48 bg-indigo-600 rounded-full flex items-center justify-center 
          shadow-indigo-500/50 cursor-pointer user-select-none transition-all duration-100 
          ${tapAnimation ? 'tap-animation shadow-xl' : 'shadow-2xl hover:bg-indigo-700 active:shadow-lg'}
        `}
      >
        <Zap className={`h-24 w-24 text-yellow-300 ${tapAnimation ? 'tap-icon-bounce' : ''}`} />
      </div>

      <p className="text-gray-500 mt-4 text-sm">Нажмите, чтобы получить {playerState.clicks_per_tap} очков!</p>
      
      {/* Секция Улучшений */}
      <div className="w-full max-w-md mt-10 p-4 bg-gray-800 rounded-2xl border border-gray-700 shadow-2xl">
        <h3 className="text-xl font-semibold text-indigo-400 mb-3 flex items-center">
          <Gift className="h-5 w-5 mr-2" /> Улучшения
        </h3>
        
        <div className={`p-4 rounded-xl transition-all duration-300 
          ${canUpgrade ? 'bg-green-600 hover:bg-green-700 shadow-lg' : 'bg-gray-700 cursor-not-allowed opacity-70'}`}
        >
          <div className="flex justify-between items-center">
            <div>
              <p className="text-lg font-bold">Увеличение Clicks per Tap (+1)</p>
              <p className="text-sm mt-1">Текущий бонус: +{playerState.clicks_per_tap}</p>
            </div>
            
            <button
              onClick={handleUpgrade}
              disabled={!canUpgrade}
              className={`py-2 px-4 rounded-full font-bold transition-colors shadow-md flex items-center
                ${canUpgrade ? 'bg-white text-green-700 hover:bg-gray-200' : 'bg-gray-500 text-gray-300'}`}
              title={canUpgrade ? "" : `Необходимо ${UPGRADE_COST} очков`}
            >
              <ChevronUp className="h-4 w-4 mr-1" />
              {UPGRADE_COST.toLocaleString()}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default App;
