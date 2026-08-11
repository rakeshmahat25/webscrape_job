# Starts the Celery worker (solo pool -- Windows-safe, one task at a time).
# Requires Redis: REDIS_URL in .env
# Run beat in a separate terminal: start_beat.ps1
& ".\venv\Scripts\celery.exe" -A config worker --pool=solo -l info
