# apps/openings/views.py

import threading
from datetime import timedelta

from django.db import models
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Company, JobOpening, ScrapeRun


def dashboard(request):
    """
    Web page showing the scraped data.
    """

    jobs = (
        JobOpening.objects
        .filter(is_active=True)
        .select_related("company")
        .order_by("-first_seen_at")[:500]
    )

    recent_runs = ScrapeRun.objects.order_by("-started_at")[:10]

    companies = Company.objects.annotate(
        openings_count=models.Count(
            "openings",
            filter=models.Q(openings__is_active=True),
        )
    ).order_by("name")

    context = {
        "companies": companies,
        "openings": jobs,
        "recent_runs": recent_runs,
        "total_openings": JobOpening.objects.filter(is_active=True).count(),
        "total_companies": Company.objects.count(),
        "now": timezone.now(),
    }

    return render(request, "openings/dashboard.html", context)


@csrf_exempt
def run_scrape_now(request):
    """
    POST-only: trigger a scrape in the background and redirect back.
    """

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    from .services.scraper import run_scrape_for_active_companies

    def _runner():
        try:
            run_scrape_for_active_companies()
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()

    return JsonResponse({"triggered": True})


def latest_openings(request):
    """
    Simple API endpoint.

    Example:
        /api/openings/latest/
        /api/openings/latest/?days=3
    """

    try:
        days = int(request.GET.get("days", 3))
    except Exception:
        days = 3

    since = timezone.now() - timedelta(days=days)

    jobs = (
        JobOpening.objects
        .filter(first_seen_at__gte=since)
        .select_related("company")
        .order_by("-first_seen_at")[:500]
    )

    results = []

    for job in jobs:
        results.append(
            {
                "id": job.id,
                "company": job.company.name,
                "title": job.title,
                "url": job.url,
                "source_url": job.source_url,
                "location": job.location,
                "salary": job.salary,
                "description": job.description,
                "requirements": job.requirements,
                "first_seen_at": job.first_seen_at.isoformat(),
                "last_seen_at": job.last_seen_at.isoformat(),
            }
        )

    return JsonResponse(
        {
            "days": days,
            "count": len(results),
            "results": results,
        }
    )