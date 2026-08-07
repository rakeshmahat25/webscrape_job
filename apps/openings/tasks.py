# apps/openings/tasks.py

from celery import shared_task

from .services.scraper import run_scrape_for_active_companies


@shared_task(name="apps.openings.tasks.scrape_openings")
def scrape_openings(limit=None):
    """
    Celery task executed every 3 days.
    """

    run = run_scrape_for_active_companies(limit=limit)

    return {
        "run_id": run.id,
        "status": run.status,
        "companies_processed": run.companies_processed,
        "jobs_seen": run.jobs_seen,
        "new_jobs": run.new_jobs,
    }