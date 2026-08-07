# Windows start scripts
Write-Host "Starting Django dev server on http://127.0.0.1:8000 ..."
& ".\venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000