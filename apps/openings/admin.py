# apps/openings/admin.py

from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path

from .models import Company, JobOpening, ScrapeRun
from .services.imports import import_companies_from_csv


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "company_name",
        "website",
        "is_active",
        "last_scraped_at",
        "rate_limited_until",
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
        "career_url",
        "rate_limited_until",
        "created_at",
        "updated_at",
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="openings_company_import_csv",
            ),
        ]
        return custom_urls + urls

    def import_csv_view(self, request):
        if request.method == "POST":
            csv_file = request.FILES.get("csv_file")
            if not csv_file:
                messages.error(request, "Please choose a CSV file to upload.")
                return redirect("admin:openings_company_changelist")
            if not csv_file.name.lower().endswith(".csv"):
                messages.error(request, "Only .csv files are allowed.")
                return redirect("admin:openings_company_changelist")
            try:
                result = import_companies_from_csv(csv_file)
            except Exception as exc:
                messages.error(request, f"Import failed: {exc}")
                return redirect("admin:openings_company_changelist")
            messages.success(
                request,
                f"Created {result.created}, updated {result.updated} company/companies.",
            )
            if result.skipped_no_name or result.skipped_duplicate:
                details = ", ".join(result.skipped_names[:20])
                if len(result.skipped_names) > 20:
                    details += f" ... and {len(result.skipped_names) - 20} more"
                messages.warning(
                    request,
                    f"Skipped {result.skipped_no_name} row(s) without a name and "
                    f"{result.skipped_duplicate} duplicate row(s): {details}",
                )
            return redirect("admin:openings_company_changelist")
        return render(
            request,
            "admin/openings/company/import_csv.html",
            {"opts": self.model._meta},
        )


@admin.register(JobOpening)
class JobOpeningAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "title",
        "experience_level",
        "location",
        "salary",
        "deadline",
        "url",
        "first_seen_at",
        "last_seen_at",
        "is_active",
    )

    list_filter = (
        "is_active",
        "company",
        "experience_level",
    )

    search_fields = (
        "title",
        "url",
        "company__name",
        "company__company_name",
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
        "education",
        "deadline",
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