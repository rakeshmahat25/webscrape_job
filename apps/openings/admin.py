# apps/openings/admin.py

from django.contrib import admin

from .models import Company, JobOpening, ScrapeRun


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "company_name",
        "website",
        "is_active",
        "last_scraped_at",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
        "company_name",
        "website",
    )

    readonly_fields = (
        "last_scraped_at",
        "last_error",
        "created_at",
        "updated_at",
    )


@admin.register(JobOpening)
class JobOpeningAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "company",
        "url",
        "first_seen_at",
        "last_seen_at",
        "is_active",
    )

    list_filter = (
        "is_active",
        "company",
    )

    search_fields = (
        "title",
        "url",
        "company__name",
    )

    readonly_fields = (
        "job_key",
        "first_seen_at",
        "last_seen_at",
    )

    fields = (
        "company",
        "title",
        "url",
        "source_url",
        "location",
        "salary",
        "description",
        "requirements",
        "is_active",
        "job_key",
        "first_seen_at",
        "last_seen_at",
    )


@admin.register(ScrapeRun)
class ScrapeRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "finished_at",
        "status",
        "companies_processed",
        "jobs_seen",
        "new_jobs",
    )

    readonly_fields = (
        "started_at",
        "finished_at",
        "companies_processed",
        "jobs_seen",
        "new_jobs",
    )