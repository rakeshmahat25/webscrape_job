# apps/openings/views.py

from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Company, JobOpening, ScrapeRun
from .services.imports import import_companies_from_csv
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
    ).order_by("-openings_count", "name")


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
def run_scrape_now(request):
    """
    POST-only: Trigger a scrape in a background thread.
    Always runs directly — does not depend on Celery worker.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    import threading
    from .services.scraper import run_scrape_for_active_companies

    def _run():
        run_scrape_for_active_companies(limit=None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return JsonResponse({"triggered": True, "mode": "threaded"})


@login_required
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
                "company": job.company.display_name,
                "title": job.title,
                "url": job.url,
                "source_url": job.source_url,
                "location": job.location,
                "salary": job.salary,
                "education": job.education,
                "deadline": job.deadline,
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


@login_required
def job_search(request):
    """
    Search active openings by keyword (title, requirements, description,
    company) and filter by experience level.
    """
    query = request.GET.get("q", "").strip()
    level = request.GET.get("level", "").strip()

    jobs_qs = _active_openings_qs()

    if query:
        jobs_qs = jobs_qs.filter(
            models.Q(title__icontains=query)
            | models.Q(requirements__icontains=query)
            | models.Q(description__icontains=query)
            | models.Q(company__name__icontains=query)
            | models.Q(company__company_name__icontains=query)
        )

    if level:
        jobs_qs = jobs_qs.filter(experience_level=level)

    paginator = Paginator(jobs_qs, OPENINGS_PER_PAGE)
    jobs = _get_page(paginator, request.GET.get("page"))

    context = {
        "active_nav": "search",
        "jobs": jobs,
        "query": query,
        "level": level,
        "levels": JobOpening.ExperienceLevel.choices,
        "total_results": paginator.count,
    }

    return render(request, "openings/search.html", context)


@login_required
def import_companies(request):
    """
    Staff-only page: upload a CSV of companies to add/update them.
    Duplicate names are skipped/updated, never re-created.
    """
    if not request.user.is_staff:
        raise PermissionDenied

    result = None

    if request.method == "POST":
        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            messages.error(request, "Please choose a CSV file to upload.")
        elif not csv_file.name.lower().endswith(".csv"):
            messages.error(request, "Only .csv files are allowed.")
        else:
            try:
                result = import_companies_from_csv(csv_file)
            except Exception as exc:
                messages.error(request, f"Import failed: {exc}")

    context = {
        "active_nav": "companies",
        "result": result,
    }

    return render(request, "openings/import_companies.html", context)