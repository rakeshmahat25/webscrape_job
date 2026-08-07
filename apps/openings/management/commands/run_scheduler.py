# apps/openings/management/commands/run_scheduler.py

import time

from django.core.management.base import BaseCommand

from apps.openings.services.scraper import (
    run_scrape_for_active_companies,
)


class Command(BaseCommand):
    help = (
        "Run the scraper loop forever (no Celery/Redis needed). "
        "Scrapes every --interval-hours (default 24)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-hours",
            type=float,
            default=24,
            help="Hours between scrape runs (default 24)",
        )
        parser.add_argument(
            "--interval-minutes",
            type=float,
            default=None,
            help="Minutes between scrape runs (takes priority over --interval-hours)",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single scrape and exit",
        )

    def handle(self, *args, **options):
        if options["interval_minutes"]:
            interval_seconds = int(float(options["interval_minutes"]) * 60)
            interval_label = f"{options['interval_minutes']}m"
        else:
            interval_seconds = int(float(options["interval_hours"]) * 3600)
            interval_label = f"{options['interval_hours']}h"

        if options["once"]:
            self.run_scrape()
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Scheduler started. Running every {interval_label}. "
                "Press Ctrl+C to stop."
            )
        )

        while True:
            self.run_scrape()
            self.stdout.write(
                self.style.WARNING(
                    f"Sleeping {interval_label} until next run..."
                )
            )
            time.sleep(interval_seconds)

    def run_scrape(self):
        self.stdout.write("Running scrape...")
        run = run_scrape_for_active_companies()
        self.stdout.write(
            self.style.SUCCESS(
                f"Status: {run.status} | "
                f"Companies: {run.companies_processed} | "
                f"Jobs seen: {run.jobs_seen} | "
                f"New jobs: {run.new_jobs}"
            )
        )