#!/usr/bin/env bash

# Установка переменных окружения для логирования
set -eo pipefail

echo "Starting Gunicorn using 'python -m gunicorn' to ensure the correct Python environment is loaded."

# ИСПОЛЬЗУЕМ: 'python -m gunicorn' вместо просто 'gunicorn'
# Это гарантирует, что мы используем Python-интерпретатор,
# который установил все зависимости, включая 'firebase-admin'.
/usr/bin/env python -m gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:"$PORT"
