#!/usr/bin/env bash

# Установка переменных окружения для логирования
set -eo pipefail

echo "Executing start.sh script. Launching Gunicorn."

# Запускаем Gunicorn, используя прямую команду, что иногда более надежно
# в контексте виртуального окружения Render.
# $PORT - это переменная окружения, предоставляемая Render.
exec gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:"$PORT"
