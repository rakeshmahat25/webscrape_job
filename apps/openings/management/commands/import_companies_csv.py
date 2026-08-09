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
            help="Path to CSV file. Expected columns: sn, company_name, website",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]

        created_count = 0
        updated_count = 0
        skipped_count = 0

        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            fieldnames = reader.fieldnames
            name_header = "company_name" if "company_name" in fieldnames else "name" if "name" in fieldnames else fieldnames[0]

            for row in reader:
                name = (
                    row.get(name_header)
                    or ""
                ).strip()

                if not name:
                    skipped_count += 1
                    continue

                company_name = (
                    row.get("company_name")
                    or ""
                ).strip()

                website = (
                    row.get("website")
                    or ""
                ).strip()

                company, created = Company.objects.update_or_create(
                    name=name,
                    defaults={
                        "company_name": company_name,
                        "website": website,
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