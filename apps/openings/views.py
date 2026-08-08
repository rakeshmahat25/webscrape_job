# apps/openings/views.py

from datetime import timedelta
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Company, JobOpening, ScrapeRun
from .tasks import scrape_openings  # Import the Celery task

COMPANIES_PER_PAGE = 25
OPENINGS_PER_PAGE = 50
DASHBOARD_JOBS_PER_PAGE = 50
COMPANIES_SIDEBAR_COUNT = 8


def _get_page(paginator, page_num):
    try:
        return paginator.page(page_num)
    except (PageNotAnInteger, EmptyPage):
        return paginator.page(1)


def _active_openings_qs():
    return (
        JobOpening.objects
        .filter(is_active=True)
        .select_related("company")
        .order_by("-first_seen_at")
    )


def _companies_qs():
    return Company.objects.annotate(
        openings_count=models.Count(
            "openings",
            filter=models.Q(openings__is_active=True),
        )
    ).order_by("name")


def dashboard(request):
    """
    Web page showing the scraped data.
    Primary: active openings. Secondary: top companies + recent runs.
    """
    recent_runs = ScrapeRun.objects.order_by("-started_at")[:10]

    companies_qs = _companies_qs()
    top_companies = companies_qs[:COMPANIES_SIDEBAR_COUNT]

    openings_paginator = Paginator(
        _active_openings_qs(),
        DASHBOARD_JOBS_PER_PAGE,
    )
    openings = _get_page(openings_paginator, request.GET.get("openings_page"))

    context = {
        "active_nav": "dashboard",
        "openings": openings,
        "top_companies": top_companies,
        "recent_runs": recent_runs,
        "total_openings": openings_paginator.count,
        "total_companies": companies_qs.count(),
        "now": timezone.now(),
        "run_openings_count": openings_paginator.count,
    }

    return render(request, "openings/dashboard.html", context)


def company_list(request):
    """
    List all companies (secondary page), paginated.
    """
    companies_qs = _companies_qs()

    paginator = Paginator(companies_qs, COMPANIES_PER_PAGE)
    companies = _get_page(paginator, request.GET.get("page"))

    context = {
        "active_nav": "companies",
        "companies": companies,
        "total_companies": paginator.count,
    }

    return render(request, "openings/companies.html", context)


def company_detail(request, company_id):
    """
    Company profile: full info plus all active openings scraped
    from its career/vacancy pages.
    """
    company = get_object_or_404(
        Company.objects.annotate(
            openings_count=models.Count(
                "openings",
                filter=models.Q(openings__is_active=True),
            )
        ),
        pk=company_id,
    )

    openings_qs = (
        company.openings
        .filter(is_active=True)
        .order_by("-first_seen_at")
    )

    paginator = Paginator(openings_qs, OPENINGS_PER_PAGE)
    openings = _get_page(paginator, request.GET.get("page"))

    context = {
        "active_nav": "companies",
        "company": company,
        "openings": openings,
    }

    return render(request, "openings/company_detail.html", context)


def opening_detail(request, job_id):
    """
    Single job page: description, requirements, location, salary.
    """
    job = get_object_or_404(
        JobOpening.objects.select_related("company"),
        pk=job_id,
    )

    context = {
        "job": job,
    }

    return render(request, "openings/opening_detail.html", context)


@csrf_exempt
def run_scrape_now(request):
    """
    POST-only: Trigger a scrape safely through Celery in the background.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        # Dispatch asynchronously via Celery (Non-blocking)
        task = scrape_openings.delay(limit=None)
        return JsonResponse({"triggered": True, "task_id": task.id})
    except Exception as e:
        return JsonResponse({"triggered": False, "error": str(e)}, status=500)


def latest_openings(request):
    """
    Simple API endpoint.
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