celery -A config worker --pool=gevent --concurrency=10 -l info
celery -A config beat --loglevel=info