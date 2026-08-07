# Runs the scraper loop every N minutes (no Redis/Celery needed).
# Usage:
#   powershell -ExecutionPolicy Bypass -File start_scheduler.ps1          # every 24 hours
#   powershell -ExecutionPolicy Bypass -File start_scheduler.ps1 5        # every 5 minutes
param(
    [double]$IntervalMinutes = 1440
)
& ".\venv\Scripts\python.exe" manage.py run_scheduler --interval-minutes $IntervalMinutes