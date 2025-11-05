# start.sh
# Запускаем Gunicorn с Uvicorn Worker, который необходим для асинхронного Starlette/FastAPI
gunicorn api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
