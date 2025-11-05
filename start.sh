#!/usr/bin/env bash

# Установка переменных окружения для логирования
set -eo pipefail

echo "Executing custom start.sh script. Attempting to locate Gunicorn in PATH."

# Использование exec для замены текущего процесса Gunicorn.
# Render, как правило, добавляет исполняемые файлы venv в PATH, 
# но если этого не происходит, это и есть причина ошибки.
# Мы вернемся к команде, которую логировал Render, но через скрипт.
# NOTE: Если это не сработает, нужно будет вручную изменить команду START COMMAND на Render!
exec gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:"$PORT"
