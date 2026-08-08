# config/urls.py

from django.contrib import admin
from django.urls import path

from apps.openings import views as openings_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "",
        openings_views.dashboard,
        name="dashboard",
    ),
    path(
        "companies/",
        openings_views.company_list,
        name="company-list",
    ),
    path(
        "companies/<int:company_id>/",
        openings_views.company_detail,
        name="company-detail",
    ),
    path(
        "openings/<int:job_id>/",
        openings_views.opening_detail,
        name="opening-detail",
    ),
    path(
        "api/openings/latest/",
        openings_views.latest_openings,
        name="latest-openings",
    ),
    path(
        "run-scrape/",
        openings_views.run_scrape_now,
        name="run-scrape",
    ),
]