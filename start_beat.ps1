# Starts Celery Beat (scheduled tasks, e.g. scrape every 3 days).
# Requires Redis: REDIS_URL in .env
# Run the worker in a separate terminal: start_worker.ps1
& ".\venv\Scripts\celery.exe" -A config beat -l info
