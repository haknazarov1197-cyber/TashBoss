import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Check, Trash2, Edit, X } from 'lucide-react';

// URL вашего развернутого бэкенда FastAPI
const API_BASE_URL = 'https://tashboss.onrender.com';

/**
 * Хук для выполнения fetch-запросов
 * @param {string} url - Относительный путь к эндпоинту
 * @param {string} method - Метод HTTP
 * @param {Object} body - Тело запроса (для POST/PUT)
 * @returns {Promise<Object>}
 */
const useApiRequest = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const request = useCallback(async (url, method = 'GET', body = null) => {
    setLoading(true);
    setError(null);
    try {
      const options = {
        method,
        headers: {
          'Content-Type': 'application/json',
        },
      };

      if (body) {
        options.body = JSON.stringify(body);
      }

      const response = await fetch(`${API_BASE_URL}${url}`, options);
      
      if (response.status === 204) {
          return null; // Для DELETE
      }

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || `API error: ${response.status}`);
      }

      return data;
    } catch (err) {
      console.error('API Request Failed:', err);
      setError(err.message || 'Произошла неизвестная ошибка сети.');
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  return { request, loading, error, clearError: () => setError(null) };
};

// --- Компонент UI элементов ---

const Button = ({ children, onClick, className = '', disabled = false, icon: Icon, type = 'button' }) => (
  <button
    type={type}
    onClick={onClick}
    disabled={disabled}
    className={`flex items-center justify-center p-2 rounded-lg transition-all duration-200 shadow-md 
                ${disabled ? 'bg-gray-300 text-gray-500 cursor-not-allowed' : 'hover:shadow-lg active:scale-[0.98]'}
                ${className}`}
  >
    {Icon && <Icon className="w-5 h-5 mr-1" />}
    {children}
  </button>
);

const LoadingSpinner = () => (
  <div className="flex justify-center items-center p-4">
    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
    <span className="ml-3 text-indigo-600">Загрузка...</span>
  </div>
);

const ErrorMessage = ({ message, onDismiss }) => (
  <div className="bg-red-100 border-l-4 border-red-500 text-red-700 p-4 rounded-lg shadow-md mb-4 flex justify-between items-center">
    <p>{message}</p>
    <button onClick={onDismiss} className="text-red-500 hover:text-red-800">
      <X className="w-5 h-5" />
    </button>
  </div>
);


// --- Компонент одной Задачи ---

const TaskItem = ({ task, onUpdate, onDelete, isUpdating }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description || '');

  const handleSave = () => {
    if (title.trim() === '') return;
    onUpdate(task.id, { title, description, is_done: task.is_done });
    setIsEditing(false);
  };

  const toggleDone = () => {
    onUpdate(task.id, { is_done: !task.is_done });
  };

  return (
    <div className={`p-4 mb-3 rounded-xl shadow-lg transition-all duration-300
                    ${task.is_done ? 'bg-gray-100 border-l-8 border-green-500' : 'bg-white border-l-8 border-indigo-400'}
                    ${isUpdating ? 'opacity-50' : ''}`}
    >
      {isEditing ? (
        // Режим редактирования
        <div className="flex flex-col space-y-3">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="p-2 border border-indigo-300 rounded-lg text-lg font-semibold focus:ring-indigo-500 focus:border-indigo-500"
            placeholder="Заголовок"
            disabled={isUpdating}
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="p-2 border border-indigo-300 rounded-lg text-sm focus:ring-indigo-500 focus:border-indigo-500 resize-none"
            placeholder="Описание (необязательно)"
            rows="3"
            disabled={isUpdating}
          />
          <div className="flex space-x-2 justify-end">
            <Button 
                onClick={() => setIsEditing(false)} 
                className="bg-gray-500 text-white hover:bg-gray-600"
                disabled={isUpdating}
                icon={X}
            >
              Отмена
            </Button>
            <Button 
                onClick={handleSave} 
                className="bg-green-500 text-white hover:bg-green-600"
                disabled={isUpdating || title.trim() === ''}
                icon={Check}
            >
              Сохранить
            </Button>
          </div>
        </div>
      ) : (
        // Режим просмотра
        <div className="flex items-start justify-between">
          <div 
            className="flex-1 cursor-pointer" 
            onClick={toggleDone}
          >
            <h3 className={`text-lg font-semibold ${task.is_done ? 'line-through text-gray-500' : 'text-gray-800'}`}>
              {task.title}
            </h3>
            {task.description && (
              <p className={`text-sm mt-1 text-gray-600 ${task.is_done ? 'line-through text-gray-400' : ''}`}>
                {task.description}
              </p>
            )}
          </div>
          
          <div className="flex space-x-2 ml-4 flex-shrink-0">
            {/* Кнопка выполнения/отмены */}
            <Button 
              onClick={toggleDone} 
              className={task.is_done ? 'bg-yellow-500 text-white hover:bg-yellow-600' : 'bg-green-500 text-white hover:bg-green-600'}
              disabled={isUpdating}
              icon={Check}
            >
              {task.is_done ? 'Отменить' : 'Готово'}
            </Button>
            
            {/* Кнопка редактирования */}
            <Button 
              onClick={() => setIsEditing(true)} 
              className="bg-indigo-500 text-white hover:bg-indigo-600"
              disabled={isUpdating}
              icon={Edit}
            >
              Изменить
            </Button>
            
            {/* Кнопка удаления */}
            <Button 
              onClick={() => onDelete(task.id)} 
              className="bg-red-500 text-white hover:bg-red-600"
              disabled={isUpdating}
              icon={Trash2}
            >
              Удалить
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};


// --- Компонент добавления новой Задачи ---

const AddTaskForm = ({ onAddTask, isAdding }) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (title.trim() === '') return;

    onAddTask({
      title: title.trim(),
      description: description.trim(),
    });

    // Очистка полей
    setTitle('');
    setDescription('');
  };

  return (
    <div className="p-6 bg-white rounded-xl shadow-2xl mb-6">
      <h2 className="text-xl font-bold text-indigo-700 mb-4">Добавить новую задачу</h2>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Заголовок задачи (обязательно)"
          className="w-full p-3 border border-gray-300 rounded-lg focus:ring-indigo-500 focus:border-indigo-500 transition-shadow"
          disabled={isAdding}
        />
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Описание задачи (необязательно)"
          rows="2"
          className="w-full p-3 border border-gray-300 rounded-lg focus:ring-indigo-500 focus:border-indigo-500 resize-none transition-shadow"
          disabled={isAdding}
        />
        <Button 
          type="submit" 
          disabled={isAdding || title.trim() === ''}
          className="w-full bg-indigo-600 text-white hover:bg-indigo-700"
          icon={Plus}
        >
          {isAdding ? 'Добавление...' : 'Добавить Задачу'}
        </Button>
      </form>
    </div>
  );
};


// --- Главный компонент Приложения ---

const App = () => {
  const [tasks, setTasks] = useState([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [pendingTasks, setPendingTasks] = useState(new Set()); // ID задач, которые в процессе обновления/удаления

  const { request, loading, error, clearError } = useApiRequest();

  // 1. Получение всех задач при загрузке
  const fetchTasks = useCallback(async () => {
    try {
      const data = await request('/tasks');
      setTasks(data || []);
    } catch (e) {
      console.error('Failed to fetch tasks:', e);
    } finally {
      setInitialLoading(false);
    }
  }, [request]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // 2. Добавление новой задачи
  const handleAddTask = async (newTaskData) => {
    try {
      // Добавляем временный ID для блокировки формы
      const tempId = 'new-task-temp-' + Date.now(); 
      setPendingTasks(prev => new Set(prev).add(tempId));

      const createdTask = await request('/tasks', 'POST', newTaskData);
      
      // Обновляем список задач
      setTasks(prevTasks => [createdTask, ...prevTasks]);
    } catch (e) {
      // Ошибка будет показана через ErrorMessage
    } finally {
      setPendingTasks(prev => {
        const newSet = new Set(prev);
        newSet.delete(tempId);
        return newSet;
      });
    }
  };

  // 3. Обновление существующей задачи
  const handleUpdateTask = async (id, updates) => {
    if (pendingTasks.has(id)) return;

    try {
      setPendingTasks(prev => new Set(prev).add(id));
      const updatedTask = await request(`/tasks/${id}`, 'PUT', updates);
      
      // Обновляем задачу в списке
      setTasks(prevTasks => 
        prevTasks.map(task => (task.id === id ? updatedTask : task))
      );
    } catch (e) {
      // Ошибка будет показана через ErrorMessage
    } finally {
      setPendingTasks(prev => {
        const newSet = new Set(prev);
        newSet.delete(id);
        return newSet;
      });
    }
  };

  // 4. Удаление задачи
  const handleDeleteTask = async (id) => {
    if (pendingTasks.has(id)) return;
    
    // Подтверждение перед удалением (заменяем window.confirm на простой console log)
    if (!window.confirm('Вы уверены, что хотите удалить эту задачу?')) {
        return;
    }

    try {
      setPendingTasks(prev => new Set(prev).add(id));
      await request(`/tasks/${id}`, 'DELETE');
      
      // Удаляем задачу из списка
      setTasks(prevTasks => prevTasks.filter(task => task.id !== id));
    } catch (e) {
      // Ошибка будет показана через ErrorMessage
    } finally {
      setPendingTasks(prev => {
        const newSet = new Set(prev);
        newSet.delete(id);
        return newSet;
      });
    }
  };
  
  // Разделение задач на активные и завершенные
  const activeTasks = tasks.filter(task => !task.is_done);
  const completedTasks = tasks.filter(task => task.is_done);
  
  const isAdding = pendingTasks.has(tasks.find(t => t.id && t.id.startsWith('new-task-temp'))?.id);

  return (
    <div className="min-h-screen bg-gray-50 p-4 sm:p-8 font-sans">
      <script src="https://cdn.tailwindcss.com"></script>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        body { font-family: 'Inter', sans-serif; }
      `}</style>

      <div className="max-w-4xl mx-auto">
        <header className="text-center py-6 mb-6 bg-white rounded-xl shadow-lg">
          <h1 className="text-4xl font-extrabold text-indigo-700">
            Task Manager
          </h1>
          <p className="text-gray-500 mt-2">
            FastAPI (Python) + Firestore + React
          </p>
        </header>
        
        {error && <ErrorMessage message={`Ошибка API: ${error}`} onDismiss={clearError} />}
        
        {/* Форма добавления */}
        <AddTaskForm onAddTask={handleAddTask} isAdding={isAdding} />

        {/* Индикатор загрузки при первом запуске */}
        {initialLoading && <LoadingSpinner />}
        
        {/* Основной список задач */}
        <div className="space-y-6">
          {/* Активные задачи */}
          <div className="bg-white p-6 rounded-xl shadow-2xl">
            <h2 className="text-2xl font-bold text-indigo-700 mb-4 flex items-center">
              Активные Задачи ({activeTasks.length})
            </h2>
            {activeTasks.length === 0 && !initialLoading && (
              <p className="text-gray-500 italic">Нет активных задач. Время создать что-то новое!</p>
            )}
            <div className="space-y-3">
              {activeTasks.map(task => (
                <TaskItem
                  key={task.id}
                  task={task}
                  onUpdate={handleUpdateTask}
                  onDelete={handleDeleteTask}
                  isUpdating={pendingTasks.has(task.id)}
                />
              ))}
            </div>
          </div>

          {/* Завершенные задачи */}
          <div className="bg-white p-6 rounded-xl shadow-2xl opacity-70">
            <h2 className="text-2xl font-bold text-green-700 mb-4">
              Завершенные ({completedTasks.length})
            </h2>
            <div className="space-y-3">
              {completedTasks.map(task => (
                <TaskItem
                  key={task.id}
                  task={task}
                  onUpdate={handleUpdateTask}
                  onDelete={handleDeleteTask}
                  isUpdating={pendingTasks.has(task.id)}
                />
              ))}
            </div>
          </div>
        </div>
        
        {/* Индикатор общих операций */}
        {loading && !initialLoading && (
            <div className="fixed bottom-4 right-4 bg-indigo-600 text-white py-2 px-4 rounded-full shadow-xl">
                Выполнение операции...
            </div>
        )}
      </div>
    </div>
  );
};

export default App;
