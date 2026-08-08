# apps/openings/tasks.py

import os
import requests
from django.conf import settings
from celery import shared_task

from .services.scraper import run_scrape_for_active_companies


def send_new_jobs_notification(run, new_jobs_count):
    """
    Send an alert webhook if new jobs are found.
    Configure WEBHOOK_URL in your settings.py or use environment variables.
    """
    # Safely fetch WEBHOOK_URL from Django settings or fallback to environment variable
    webhook_url = getattr(settings, "WEBHOOK_URL", None)
    if not webhook_url:
        webhook_url = os.getenv("NOTIFICATION_WEBHOOK_URL")

    if not webhook_url or new_jobs_count <= 0:
        return

    message = (
        f"*Job Monitor Update*\n"
        f"A scrape run just completed successfully!\n"
        f"• Companies Processed: {run.companies_processed}\n"
        f"• Jobs Seen: {run.jobs_seen}\n"
        f"• **New Jobs Found:** {new_jobs_count}"
    )

    try:
        if "discord" in webhook_url or "slack" in webhook_url:
            requests.post(webhook_url, json={"content": message}, timeout=10)
        elif "telegram" in webhook_url:
            requests.post(webhook_url, json={"text": message, "parse_mode": "Markdown"}, timeout=10)
        else:
            # Generic webhook fallback
            requests.post(webhook_url, json={"message": message}, timeout=10)
    except Exception:
        pass


@shared_task(name="apps.openings.tasks.scrape_openings")
def scrape_openings(limit=None):
    """
    Celery task executed via beat schedule or manually.
    """
    run = run_scrape_for_active_companies(limit=limit)

    # Fire alert if new jobs were discovered
    if run.status == "success" and run.new_jobs > 0:
        send_new_jobs_notification(run, run.new_jobs)

    return {
        "run_id": run.id,
        "status": run.status,
        "companies_processed": run.companies_processed,
        "jobs_seen": run.jobs_seen,
        "new_jobs": run.new_jobs,
    }