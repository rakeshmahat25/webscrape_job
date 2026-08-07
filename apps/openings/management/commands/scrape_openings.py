# apps/openings/management/commands/scrape_openings.py

from django.core.management.base import BaseCommand

from apps.openings.services.scraper import (
    run_scrape_for_active_companies,
)


class Command(BaseCommand):
    help = "Scrape job openings from active companies"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of companies to scrape",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")

        self.stdout.write("Starting job opening scrape...")

        run = run_scrape_for_active_companies(limit=limit)

        self.stdout.write(
            self.style.SUCCESS(
                f"Status: {run.status}\n"
                f"Companies processed: {run.companies_processed}\n"
                f"Jobs seen: {run.jobs_seen}\n"
                f"New jobs: {run.new_jobs}\n"
            )
        )

        if run.message:
            self.stdout.write(
                self.style.WARNING(run.message)
            )