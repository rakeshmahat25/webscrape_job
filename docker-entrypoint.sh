#!/bin/sh
set -e

# Only the web process runs migrate/collectstatic; worker and beat skip them.
case "$1" in
  gunicorn)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    ;;
esac

exec "$@"