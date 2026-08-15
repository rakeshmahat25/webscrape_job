# apps/openings/management/commands/import_companies_csv.py

from django.core.management.base import BaseCommand

from apps.openings.services.imports import import_companies_from_csv


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

        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            result = import_companies_from_csv(f)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created: {result.created}, "
                f"Updated: {result.updated}, "
                f"Skipped: {result.skipped_no_name + result.skipped_duplicate}"
            )
        )

        if result.skipped_duplicate:
            self.stdout.write(
                self.style.WARNING(
                    f"Duplicate rows skipped: {', '.join(result.skipped_names[:20])}"
                )
            )
            if len(result.skipped_names) > 20:
                self.stdout.write(
                    self.style.WARNING(
                        f"... and {len(result.skipped_names) - 20} more"
                    )
                )