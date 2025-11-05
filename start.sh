#!/usr/bin/env bash

# Установка переменных окружения для логирования
set -eo pipefail

echo "Executing start.sh script. Launching Gunicorn via Python environment."

# КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: 
# Запускаем Gunicorn через модуль Python (python -m gunicorn), 
# чтобы гарантировать использование виртуального окружения Render, где установлены все пакеты.
# $PORT - это переменная окружения, предоставляемая Render.
exec python -m gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:"$PORT"
