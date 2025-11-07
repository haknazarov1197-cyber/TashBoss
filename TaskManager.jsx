import React, { useState, useEffect, useCallback } from 'react';
import { Trash2, Edit2, CheckCircle, Circle, Plus, AlertTriangle, Loader, RefreshCw } from 'lucide-react';

// --- КОНФИГУРАЦИЯ API ---
// Предполагаем, что API запущен на том же хосте, но на порту 8000
const API_BASE_URL = window.location.origin.replace('3000', '8000') || 'http://localhost:8000';

/**
 * Вспомогательная функция для выполнения запросов к API.
 * @param {string} endpoint - Конечная точка API.
 * @param {string} method - HTTP-метод (GET, POST, PUT, DELETE).
 * @param {object} [data=null] - Тело запроса.
 * @returns {Promise<any>} Ответ API.
 */
async function apiFetch(endpoint, method = 'GET', data = null) {
  const url = `${API_BASE_URL}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
  };

  const config = {
    method,
    headers,
    ...(data && { body: JSON.stringify(data) }),
  };

  try {
    const response = await fetch(url, config);

    if (method === 'DELETE' && response.status === 204) {
      return null;
    }

    const jsonResponse = await response.json();

    if (!response.ok) {
      const errorDetail = jsonResponse.detail || 'Неизвестная ошибка API';
      throw new Error(`Ошибка ${response.status}: ${errorDetail}`);
    }

    return jsonResponse;
  } catch (error) {
    console.error('API Fetch Error:', error);
    throw error;
  }
}

// --- Компонент одного элемента задачи ---

const TaskItem = ({ task, onUpdate, onDelete }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [newTitle, setNewTitle] = useState(task.title);
  const [newDescription, setNewDescription] = useState(task.description || '');

  const toggleDone = useCallback(() => {
    onUpdate(task.id, { is_done: !task.is_done });
  }, [task.id, task.is_done, onUpdate]);

  const handleEditSubmit = useCallback(() => {
    if (newTitle.trim() === '') return;
    onUpdate(task.id, { title: newTitle.trim(), description: newDescription.trim() });
    setIsEditing(false);
  }, [task.id, newTitle, newDescription, onUpdate]);

  const formatDate = (timestamp) => {
    if (!timestamp) return '—';
    return new Date(timestamp * 1000).toLocaleString('ru-RU', {
      year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  };

  return (
    <div className={`
      flex flex-col p-4 mb-3 rounded-xl shadow-lg transition-all duration-300
      ${task.is_done ? 'bg-gray-100 border-l-8 border-green-500' : 'bg-white border-l-8 border-indigo-500 hover:shadow-xl'}
    `}>
      {isEditing ? (
        <div className="flex flex-col gap-2">
          <input
            className="text-xl font-semibold p-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleEditSubmit()}
            autoFocus
          />
          <textarea
            className="text-gray-600 p-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            rows="2"
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
          />
          <button
            onClick={handleEditSubmit}
            className="mt-2 py-2 px-4 bg-green-600 text-white font-bold rounded-lg hover:bg-green-700 transition-colors"
          >
            Сохранить
          </button>
        </div>
      ) : (
        <div className="flex flex-col">
          {/* Main Content & Toggle */}
          <div className="flex items-start justify-between">
            <div className="flex-1 cursor-pointer" onClick={toggleDone}>
              <h3 className={`text-xl font-bold ${task.is_done ? 'line-through text-gray-500' : 'text-gray-900'}`}>
                <span className="inline-block mr-3">
                  {task.is_done ? <CheckCircle className="text-green-500 inline h-5 w-5" /> : <Circle className="text-indigo-400 inline h-5 w-5" />}
                </span>
                {task.title}
              </h3>
              {(task.description || '').trim().length > 0 && (
                <p className={`text-sm mt-1 ml-8 ${task.is_done ? 'text-gray-400' : 'text-gray-600'}`}>{task.description}</p>
              )}
            </div>

            {/* Action Buttons */}
            <div className="flex-shrink-0 flex space-x-2 ml-4">
              <button
                onClick={() => setIsEditing(true)}
                className="p-2 text-indigo-600 hover:text-indigo-800 transition-colors rounded-full hover:bg-indigo-100"
                title="Редактировать"
              >
                <Edit2 className="h-5 w-5" />
              </button>
              <button
                onClick={() => onDelete(task.id)}
                className="p-2 text-red-600 hover:text-red-800 transition-colors rounded-full hover:bg-red-100"
                title="Удалить"
              >
                <Trash2 className="h-5 w-5" />
              </button>
            </div>
          </div>
          
          {/* Metadata */}
          <div className="flex justify-end text-xs text-gray-500 mt-2 border-t pt-2">
            <p>Создано: {formatDate(task.created_at)}</p>
            {task.updated_at && task.updated_at !== task.created_at && (
              <p className="ml-4">Обновлено: {formatDate(task.updated_at)}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// --- Компонент формы добавления задачи ---

const TaskForm = ({ onTaskCreated }) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (title.trim() === '') return;

    setIsSubmitting(true);
    const newTask = { title: title.trim(), description: description.trim() };

    try {
      const createdTask = await apiFetch('/tasks', 'POST', newTask);
      onTaskCreated(createdTask);
      setTitle('');
      setDescription('');
    } catch (error) {
      // Заменил alert на более безопасный вывод в консоль для Canvas
      console.error(`Не удалось создать задачу: ${error.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="p-4 bg-indigo-50 border border-indigo-200 rounded-xl shadow-inner mb-6">
      <h2 className="text-2xl font-bold text-indigo-700 mb-4">Добавить новую задачу</h2>
      <div className="mb-3">
        <input
          type="text"
          placeholder="Заголовок задачи (обязательно)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full p-3 border-2 border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 text-gray-800"
          required
        />
      </div>
      <div className="mb-4">
        <textarea
          placeholder="Описание задачи (необязательно)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows="3"
          className="w-full p-3 border-2 border-indigo-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-500 text-gray-800 resize-none"
        />
      </div>
      <button
        type="submit"
        disabled={!title.trim() || isSubmitting}
        className={`w-full py-3 px-4 text-white font-bold rounded-lg transition-colors flex items-center justify-center
          ${(!title.trim() || isSubmitting) ? 'bg-indigo-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 shadow-md hover:shadow-lg'}`}
      >
        {isSubmitting ? (
          <>
            <Loader className="animate-spin mr-2 h-5 w-5" /> Добавление...
          </>
        ) : (
          <>
            <Plus className="h-5 w-5 mr-2" /> Добавить задачу
          </>
        )}
      </button>
    </form>
  );
};

// --- Главный компонент приложения ---

const App = () => {
  const [tasks, setTasks] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isApiHealthy, setIsApiHealthy] = useState(false);

  // Проверка работоспособности API
  const checkApiHealth = useCallback(async () => {
    try {
      await apiFetch('/', 'GET');
      setIsApiHealthy(true);
      setError(null); // Сбросить ошибку, если она была
    } catch (e) {
      setIsApiHealthy(false);
      // Устанавливаем более мягкое сообщение об ошибке, если API недоступен
      setError('Ошибка подключения к API. Проверьте ваш бэкенд.');
      console.error('Health Check Failed:', e);
    }
  }, []);

  // Получение задач
  const fetchTasks = useCallback(async () => {
    if (!isApiHealthy) {
        setIsLoading(false);
        return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const data = await apiFetch('/tasks');
      setTasks(data);
    } catch (e) {
      // Если API здоров, но запрос к задачам падает (например, 500)
      setError(`Ошибка при загрузке задач: ${e.message}`);
    } finally {
      setIsLoading(false);
    }
  }, [isApiHealthy]);

  useEffect(() => {
    checkApiHealth();
  }, [checkApiHealth]);

  useEffect(() => {
    if (isApiHealthy) {
      fetchTasks();
    }
  }, [isApiHealthy, fetchTasks]);

  // Обработчик создания задачи (TaskForm вызывает его)
  const handleTaskCreated = (newTask) => {
    setTasks(prevTasks => [newTask, ...prevTasks]);
  };

  // Обработчик обновления задачи
  const handleTaskUpdate = async (id, update) => {
    try {
      const updatedTask = await apiFetch(`/tasks/${id}`, 'PUT', update);
      setTasks(prevTasks => prevTasks.map(t => t.id === id ? updatedTask : t));
    } catch (error) {
      console.error(`Не удалось обновить задачу: ${error.message}`);
    }
  };

  // Обработчик удаления задачи
  const handleTaskDelete = async (id) => {
    // Используем window.confirm напрямую для простоты
    if (!confirm("Вы уверены, что хотите удалить эту задачу?")) {
      return;
    }
    try {
      await apiFetch(`/tasks/${id}`, 'DELETE');
      setTasks(prevTasks => prevTasks.filter(t => t.id !== id));
    } catch (error) {
      console.error(`Не удалось удалить задачу: ${error.message}`);
    }
  };

  const tasksDone = tasks.filter(t => t.is_done);
  const tasksPending = tasks.filter(t => !t.is_done);

  // --- Рендеринг ---

  return (
    <div className="min-h-screen bg-gray-50 p-4 sm:p-8 font-sans">
      <script src="https://cdn.tailwindcss.com"></script>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap');
        body { font-family: 'Inter', sans-serif; }
      `}</style>
      
      <div className="max-w-4xl mx-auto">
        <h1 className="text-4xl font-extrabold text-gray-800 mb-6 border-b-4 border-indigo-500 pb-2">
          Менеджер Задач
        </h1>

        {/* Секция API Health */}
        <div className={`p-4 rounded-xl mb-6 shadow-md flex items-center justify-between
          ${isApiHealthy ? 'bg-green-100 border-l-4 border-green-500' : 'bg-red-100 border-l-4 border-red-500'}`}
        >
          <div className="flex items-center">
            {isApiHealthy ? (
              <CheckCircle className="text-green-600 mr-3 h-6 w-6" />
            ) : (
              <AlertTriangle className="text-red-600 mr-3 h-6 w-6" />
            )}
            <p className="text-gray-800 font-medium">
              Статус API: {isApiHealthy ? 'Активен и готов к работе' : 'Недоступен'}
            </p>
          </div>
          <button
            onClick={() => { checkApiHealth(); fetchTasks(); }}
            className="p-2 bg-white rounded-full text-indigo-600 hover:bg-indigo-50 transition-colors"
            title="Повторить проверку"
            disabled={isLoading}
          >
            <RefreshCw className={`h-5 w-5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Форма добавления задачи */}
        <TaskForm onTaskCreated={handleTaskCreated} />

        {/* Отображение ошибок */}
        {error && (
          <div className="p-4 bg-yellow-100 text-yellow-800 rounded-lg flex items-center mb-6">
            <AlertTriangle className="h-5 w-5 mr-3" />
            {error}
          </div>
        )}

        {/* Список задач */}
        <h2 className="text-3xl font-bold text-gray-800 mb-4">
          Текущие Задачи ({tasksPending.length})
        </h2>
        {isLoading && isApiHealthy ? (
          <div className="flex items-center justify-center p-10 bg-white rounded-xl shadow-lg">
            <Loader className="animate-spin h-8 w-8 text-indigo-600 mr-3" />
            <p className="text-lg text-indigo-600">Загрузка задач...</p>
          </div>
        ) : tasksPending.length > 0 ? (
          tasksPending.map(task => (
            <TaskItem 
              key={task.id} 
              task={task} 
              onUpdate={handleTaskUpdate} 
              onDelete={handleTaskDelete} 
            />
          ))
        ) : 
          !error && <p className="p-4 text-center bg-white rounded-xl shadow-md text-gray-500">Пока нет активных задач. Отлично!</p>
        }

        {tasksDone.length > 0 && (
          <h2 className="text-3xl font-bold text-gray-800 mt-8 mb-4 border-t pt-6">
            Завершенные Задачи ({tasksDone.length})
          </h2>
        )}
        
        {tasksDone.map(task => (
          <TaskItem 
            key={task.id} 
            task={task} 
            onUpdate={handleTaskUpdate} 
            onDelete={handleTaskDelete} 
          />
        ))}

      </div>
    </div>
  );
};

export default App;
