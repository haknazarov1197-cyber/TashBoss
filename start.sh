#!/usr/bin/env bash

# Установка переменных окружения для логирования
set -eo pipefail

echo "Executing start.sh script. Launching Gunicorn via 'python -m gunicorn' to ensure all libraries are found."

# Используем 'python -m gunicorn' для запуска в контексте установленных библиотек
/usr/bin/env python -m gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:"$PORT"
