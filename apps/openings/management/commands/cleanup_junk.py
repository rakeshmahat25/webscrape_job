# apps/openings/management/commands/cleanup_junk.py

import re

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.openings.models import JobOpening


JUNK_TITLES = {
    "home",
    "why us",
    "hire staff",
    "get hired",
    "start hiring",
    "success stories",
    "current vacancies",
    "view details",
    "general delivery conditions",
    "about giftmandu",
    "why work with giftmandu",
    "what do we look for in our potential employees?",
    "careers at giftmandu",
    "nepalese rupee",
    "us dollar",
}

JUNK_URL_PATTERNS = [
    r"setCurrencyId",
    r"/generalDeliveryConditions$",
]


class Command(BaseCommand):
    help = "Deactivate junk job openings scraped from nav links and boilerplate"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deactivated without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        qs = JobOpening.objects.filter(is_active=True)

        # 1. Exact title matches
        title_filter = Q()
        for t in JUNK_TITLES:
            title_filter |= Q(title__iexact=t)

        junk_by_title = qs.filter(title_filter)

        # 2. URL pattern matches
        url_filter = Q()
        for pat in JUNK_URL_PATTERNS:
            url_filter |= Q(url__iregex=pat)

        junk_by_url = qs.filter(url_filter)

        # 3. Gift product category links (contain "care" in title, URL is
        #    a product/category page on giftmandu.com)
        junk_products = qs.filter(
            url__icontains="giftmandu.com",
            title__iregex=r"\bcare\b",
        ).exclude(
            title__iregex=r"\b(manager|officer|assistant|designer|developer|engineer|trainee|staff|coordinator)\b"
        )

        # 4. Titles that are pure nav (all caps or very short, no role keyword)
        junk_nav = qs.filter(
            title__iregex=r"^(home|about|services|contact|why us|hire staff|get hired|start hiring|success stories|current vacancies)$"
        )

        # Combine all junk querysets (deduplicate)
        all_junk_ids = set()
        for qs_junk in [junk_by_title, junk_by_url, junk_products, junk_nav]:
            all_junk_ids.update(qs_junk.values_list("id", flat=True))

        junk_qs = qs.filter(id__in=all_junk_ids)

        count = junk_qs.count()

        if dry_run:
            self.stdout.write(f"Would deactivate {count} junk openings:")
            for job in junk_qs.select_related("company")[:50]:
                self.stdout.write(f"  [{job.id}] {job.company.display_name}: {job.title} -> {job.url}")
            if count > 50:
                self.stdout.write(f"  ... and {count - 50} more")
        else:
            junk_qs.update(is_active=False)
            self.stdout.write(
                self.style.SUCCESS(f"Deactivated {count} junk openings")
            )
