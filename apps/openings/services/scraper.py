# apps/openings/services/scraper.py

import os
import re
import time
from urllib.parse import urlparse

# Playwright's sync API runs an asyncio loop in the same thread,
# which makes Django think we are in an async context. Allow ORM calls.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.utils import timezone

from playwright.sync_api import sync_playwright

from apps.openings.models import Company, JobOpening, ScrapeRun


USER_AGENT = (
    "Mozilla/5.0 (compatible; DjangoJobMonitorBot/1.0; "
    "+https://example.com/contact)"
)

PAGE_TIMEOUT = 45000

REQUEST_DELAY_SECONDS = 4


CAREER_PATHS = [
    "/careers",
    "/career",
    "/jobs",
    "/job",
    "/vacancies",
    "/vacancy",
    "/openings",
    "/opening",
    "/join-us",
    "/joinus",
    "/work-with-us",
    "/employment",
    "/hiring",
    "/recruitment",
    "/opportunities",
]


JOB_KEYWORD_RE = re.compile(
    r"(?:\bjobs?\b|\bcares?\b|vacanc(?:y|ies)|openings?|apply|position|hiring|employment|employee|careers?|recruit\w*|\bjoin\b|opportunit\w*|staff\w*)",
    re.IGNORECASE,
)


ROLE_KEYWORD_RE = re.compile(
    r"\b(developer|engineer|designer|manager|officer|assistant|analyst|consultant|specialist|executive|tester|qa|marketing|sales|accountant|writer|editor|videographer|technician|mechanic|electrician|nurse|receptionist|administrator|internship|interns)\b",
    re.IGNORECASE,
)


IGNORE_TITLES = {
    "career",
    "careers",
    "job",
    "jobs",
    "vacancy",
    "vacancies",
    "opening",
    "openings",
    "apply",
    "apply now",
    "view all",
    "view more",
    "see more",
    "learn more",
    "read more",
    "click here",
    "here",
}


SKIP_DOMAINS = (
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
)


def clean_title(title: str) -> str:
    """
    Clean scraped title text.
    """

    if not title:
        return ""

    title = re.sub(r"\s+", " ", title)
    title = title.strip()

    return title[:500]


def normalize_url(url: str) -> str:
    """
    Normalize scraped URL.
    """

    if not url:
        return ""

    url = url.strip()

    return url[:1000]


def ensure_absolute_url(url: str) -> str:
    """
    Add https:// if missing.
    """

    if not url:
        return ""

    url = url.strip()

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    return url


def title_from_url(url: str) -> str:
    """
    Try to generate a title from URL slug.

    Example:
        /jobs/python-developer
        => Python Developer
    """

    try:
        path = urlparse(url).path.rstrip("/")

        parts = [part for part in path.split("/") if part]

        if not parts:
            return ""

        last_part = parts[-1]

        title = last_part.replace("-", " ").replace("_", " ").strip()

        if not title:
            return ""

        return clean_title(title.title())

    except Exception:
        return ""


def should_skip_url(url: str) -> bool:
    """
    Skip useless/social URLs.
    """

    if not url:
        return True

    lower_url = url.lower()

    if lower_url.startswith(("mailto:", "tel:", "javascript:", "#")):
        return True

    if lower_url.rstrip("/").endswith("#"):
        return True

    for domain in SKIP_DOMAINS:
        if domain in lower_url:
            return True

    return False


def is_job_link(title: str, url: str) -> bool:
    """
    Decide whether a link looks like a job opening.
    """

    if should_skip_url(url):
        return False

    title_lower = title.lower()

    if title_lower in IGNORE_TITLES:
        return False

    if len(title) < 4:
        return False

    if JOB_KEYWORD_RE.search(title):
        return True

    if JOB_KEYWORD_RE.search(url):
        return True

    if ROLE_KEYWORD_RE.search(title):
        return True

    return False


def page_has_job_signal(page) -> bool:
    """
    Check if page likely contains career/job content.
    """

    try:
        title = page.title()
    except Exception:
        title = ""

    try:
        body = page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )
    except Exception:
        body = ""

    text = f"{title} {body}".lower()

    keywords = [
        "career",
        "job",
        "vacancy",
        "opening",
        "apply",
        "hiring",
        "join",
    ]

    return any(keyword in text for keyword in keywords)


def find_career_page(page, website: str):
    """
    Try common career paths.

    Examples:
        /careers
        /jobs
        /vacancies
    """

    if not website:
        return None

    website = ensure_absolute_url(website)
    website = website.rstrip("/")

    candidates = [website]

    for path in CAREER_PATHS:
        candidates.append(website + path)

    for url in candidates:
        try:
            response = page.goto(
                url,
                timeout=PAGE_TIMEOUT,
                wait_until="domcontentloaded",
            )

            if response and response.ok and page_has_job_signal(page):
                return url

        except Exception:
            continue

    return None


def extract_job_links(page, source_url: str):
    """
    Extract possible job links from a page.
    """

    try:
        page.goto(
            source_url,
            timeout=PAGE_TIMEOUT,
            wait_until="domcontentloaded",
        )

        page.wait_for_timeout(2500)

        # Scroll to trigger lazy-loaded content.
        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(800)

        raw_links = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || a.title || a.getAttribute('aria-label') || '')
                        .trim()
                        .replace(/\\s+/g, ' '),
                href: a.href
            }))
            """
        )

    except Exception:
        return []

    results = []
    seen_urls = set()

    for link in raw_links:
        title = clean_title(link.get("text", ""))
        url = normalize_url(link.get("href", ""))

        if should_skip_url(url):
            continue

        # If title is generic like "Career", try URL slug.
        if title.lower() in IGNORE_TITLES:
            generated_title = title_from_url(url)

            if generated_title:
                title = generated_title

        if not is_job_link(title, url):
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)

        results.append(
            {
                "title": title[:500],
                "url": url[:1000],
                "source_url": source_url[:1000],
            }
        )

    return results


def extract_job_details(page, job_url: str):
    """
    Open a single job page and extract its details:
    description, requirements, location, salary.

    Falls back to a plain-text scrape when no structured content
    is found. Returns a dict with string values.
    """

    details = {
        "description": "",
        "requirements": "",
        "location": "",
        "salary": "",
    }

    if not job_url:
        return details

    try:
        page.goto(
            job_url,
            timeout=PAGE_TIMEOUT,
            wait_until="domcontentloaded",
        )

        page.wait_for_timeout(2000)

        for _ in range(3):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(500)

        body_text = page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )

    except Exception:
        return details

    if not body_text:
        return details

    body_text = re.sub(r"[ \t]+", " ", body_text)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

    if len(body_text) > 120000:
        body_text = body_text[:120000]

    # Common section labels that mark requirements blocks.
    requirement_headers = [
        "requirement",
        "requirements",
        "qualification",
        "qualifications",
        "skills required",
        "required skills",
        "skills & requirements",
        "skills and requirements",
        "key skills",
        "what we look for",
        "what are we looking for",
        "what we are looking for",
        "job requirement",
        "job requirements",
        "job specification",
        "responsibilities",
        "roles and responsibilities",
        "key responsibilities",
    ]

    location_patterns = [
        r"(?:location|workplace|work from|job location|based in)\s*[:：\-]\s*([^\n]+)",
        r"located at\s+([^\n]+)",
    ]

    salary_patterns = [
        r"(?:salary|compensation|pay)\s*[:：\-]\s*([^\n]+)",
        r"(?:salary)\s+range\s*[:：\-]\s*([^\n]+)",
        r"(?:rs\.?|npr|रु)\s*[\d,.]+\s*(?:-|–|to)?\s*[\d,.]*",
    ]

    # Try to capture location and salary.
    for pattern in location_patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            details["location"] = clean_title(match.group(1))
            break

    for pattern in salary_patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            details["salary"] = clean_title(match.group(0))
            break

    # Try to find a requirements section.
    lines = body_text.split("\n")
    req_index = -1

    for i, line in enumerate(lines):
        stripped = line.strip().rstrip(":").strip().lower()

        if stripped in requirement_headers or any(
            h in stripped for h in ["requirements", "qualifications"]
        ):
            req_index = i
            break

    if req_index >= 0:
        req_text = "\n".join(
            line.strip() for line in lines[req_index + 1:] if line.strip()
        )
        details["requirements"] = clean_title(req_text)[:20000]

        # Remove the requirements block from the body.
        body_text = "\n".join(lines[:req_index]).strip()

    details["description"] = clean_title(body_text)[:40000]

    return details


def run_scrape_for_active_companies(limit=None):
    """
    Main scrape runner.

    This is called by:
    - Django management command
    - Celery task
    """

    run = ScrapeRun.objects.create(
        status=ScrapeRun.Status.RUNNING
    )

    companies_qs = Company.objects.filter(
        is_active=True
    ).order_by("name")

    if limit:
        companies = list(companies_qs[:limit])
    else:
        companies = list(companies_qs)

    processed = 0
    jobs_seen = 0
    new_jobs = 0
    errors = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True
            )

            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={
                    "width": 1366,
                    "height": 768,
                },
            )

            page = context.new_page()

            for company in companies:
                processed += 1

                try:
                    target_url = ensure_absolute_url(
                        company.career_url
                    )

                    if not target_url:
                        target_url = find_career_page(
                            page,
                            company.website,
                        )

                    if not target_url:
                        company.last_scraped_at = timezone.now()
                        company.last_error = "No career page found"
                        company.save(
                            update_fields=[
                                "last_scraped_at",
                                "last_error",
                            ]
                        )
                        continue

                    links = extract_job_links(page, target_url)

                    for item in links:
                        jobs_seen += 1

                        job_key = JobOpening.make_key(
                            company.name,
                            item["title"],
                            item["url"],
                        )

                        job, created = JobOpening.objects.get_or_create(
                            job_key=job_key,
                            defaults={
                                "company": company,
                                "title": item["title"],
                                "url": item["url"],
                                "source_url": item["source_url"],
                                "raw_title": item["title"],
                                "is_active": True,
                            },
                        )

                        if created:
                            new_jobs += 1
                        else:
                            job.is_active = True

                        # Open the job page itself to capture details.
                        details = extract_job_details(
                            page,
                            item["url"],
                        )

                        job.title = item["title"]
                        job.description = details["description"]
                        job.requirements = details["requirements"]
                        job.location = details["location"]
                        job.salary = details["salary"]

                        job.save()

                        time.sleep(REQUEST_DELAY_SECONDS)

                    company.last_scraped_at = timezone.now()
                    company.last_error = ""
                    company.save(
                        update_fields=[
                            "last_scraped_at",
                            "last_error",
                        ]
                    )

                except Exception as exc:
                    company.last_scraped_at = timezone.now()
                    company.last_error = str(exc)
                    company.save(
                        update_fields=[
                            "last_scraped_at",
                            "last_error",
                        ]
                    )

                    errors.append(f"{company.name}: {exc}")

            browser.close()

        run.status = ScrapeRun.Status.SUCCESS
        run.message = "\n".join(errors[:100])

    except Exception as exc:
        run.status = ScrapeRun.Status.FAILED
        run.message = str(exc)

    finally:
        run.finished_at = timezone.now()
        run.companies_processed = processed
        run.jobs_seen = jobs_seen
        run.new_jobs = new_jobs
        run.save()

    return run