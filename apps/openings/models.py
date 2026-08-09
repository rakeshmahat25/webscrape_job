# apps/openings/models.py

import hashlib
from django.db import models


class Company(models.Model):
    """
    Company listed in Broadway placement partner PDF/list.
    """

    name = models.CharField(max_length=255, unique=True)

    company_name = models.CharField(max_length=255, blank=True)

    website = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=True)

    last_scraped_at = models.DateTimeField(null=True, blank=True)

    last_error = models.TextField(blank=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        """Return company_name if set, otherwise fall back to name."""
        return self.company_name or self.name


class JobOpening(models.Model):
    """
    Possible job opening scraped from a company website.
    """

    company = models.ForeignKey(
        Company,
        related_name="openings",
        on_delete=models.CASCADE,
    )

    # Unique hash of company + title + URL
    job_key = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
    )

    title = models.CharField(max_length=500)

    url = models.CharField(max_length=1000)

    source_url = models.CharField(max_length=1000, blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)

    last_seen_at = models.DateTimeField(auto_now=True)

    is_active = models.BooleanField(default=True)

    raw_title = models.TextField(blank=True)

    description = models.TextField(blank=True, default="")

    requirements = models.TextField(blank=True, default="")

    location = models.CharField(max_length=500, blank=True, default="")

    salary = models.CharField(max_length=500, blank=True, default="")

    education = models.CharField(max_length=500, blank=True, default="")

    deadline = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        verbose_name = "Job Opening"
        verbose_name_plural = "Job Openings"
        ordering = ["-first_seen_at"]

    @staticmethod
    def make_key(company_name: str, title: str, url: str) -> str:
        """
        Create a unique key for a job opening.
        This helps detect new openings only.
        """

        raw = (
            f"{company_name.strip().lower()}|"
            f"{title.strip().lower()}|"
            f"{url.strip().lower()}"
        )

        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def __str__(self):
        return f"{self.company.name}: {self.title}"


class ScrapeRun(models.Model):
    """
    Logs each scraping run.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(auto_now_add=True)

    finished_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )

    companies_processed = models.PositiveIntegerField(default=0)

    jobs_seen = models.PositiveIntegerField(default=0)

    new_jobs = models.PositiveIntegerField(default=0)

    message = models.TextField(blank=True)

    class Meta:
        verbose_name = "Scrape Run"
        verbose_name_plural = "Scrape Runs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.started_at} - {self.status}"