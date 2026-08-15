# apps/openings/management/commands/backfill_experience_level.py

from django.core.management.base import BaseCommand

from apps.openings.models import JobOpening
from apps.openings.services.scraper import infer_experience_level


class Command(BaseCommand):
    help = (
        "Compute experience_level for openings that still have unknown level, "
        "using their stored title/requirements/description text. "
        "No network access is needed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many would be updated without changing data",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of rows to process per batch (default 500)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        unknown = JobOpening.ExperienceLevel.UNKNOWN
        qs = JobOpening.objects.filter(experience_level=unknown)
        total = qs.count()

        if total == 0:
            self.stdout.write("No openings with unknown level.")
            return

        self.stdout.write(f"Found {total} openings with unknown level.")

        ids = list(qs.values_list("id", flat=True))
        updated = 0
        still_unknown = 0

        for start in range(0, len(ids), batch_size):
            batch = JobOpening.objects.filter(
                id__in=ids[start:start + batch_size]
            ).iterator()
            for job in batch:
                level = infer_experience_level(
                    job.title,
                    job.requirements,
                    job.description,
                )
                if level == unknown:
                    still_unknown += 1
                    continue
                updated += 1
                if not dry_run:
                    JobOpening.objects.filter(pk=job.pk).update(
                        experience_level=level
                    )

        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {updated} openings "
                f"({still_unknown} left as unknown)."
            )
        )