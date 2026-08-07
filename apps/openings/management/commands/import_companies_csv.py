# apps/openings/management/commands/import_companies_csv.py

import csv

from django.core.management.base import BaseCommand

from apps.openings.models import Company


class Command(BaseCommand):
    help = "Import companies from CSV file"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to CSV file. Expected columns: company_name, website, career_url",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]

        created_count = 0
        updated_count = 0
        skipped_count = 0

        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for row in reader:
                name = (
                    row.get("company_name")
                    or row.get("name")
                    or ""
                ).strip()

                if not name:
                    skipped_count += 1
                    continue

                website = (
                    row.get("website")
                    or ""
                ).strip()

                career_url = (
                    row.get("career_url")
                    or ""
                ).strip()

                company, created = Company.objects.update_or_create(
                    name=name,
                    defaults={
                        "website": website,
                        "career_url": career_url,
                    },
                )

                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Created: {created_count}, "
                f"Updated: {updated_count}, "
                f"Skipped: {skipped_count}"
            )
        )