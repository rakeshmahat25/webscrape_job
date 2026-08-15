# apps/openings/services/scraper.py

import datetime as dt
import random
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests as http_requests

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import close_old_connections
from django.utils import timezone

from playwright.sync_api import sync_playwright

from apps.openings.models import Company, JobOpening, ScrapeRun


CONTACT_EMAIL = "jobmonitor-contact@example.com"

USER_AGENT = (
    "Mozilla/5.0 (compatible; DjangoJobMonitorBot/1.0; "
    f"+mailto:{CONTACT_EMAIL})"
)

HTTP_TIMEOUT = 10

ROBOTS_TIMEOUT = 5

PAGE_TIMEOUT = 15000

REQUEST_DELAY_SECONDS = 2


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
    r"\b(?:job|vacanc(?:y|ies)|openings?|position|hiring|employment|recruit\w*|staff\w*)\b",
    re.IGNORECASE,
)


ROLE_KEYWORD_RE = re.compile(
    r"\b(developer|engineer|designer|manager|officer|assistant|analyst|consultant|specialist|executive|tester|qa|marketing|sales|accountant|writer|editor|videographer|technician|mechanic|electrician|nurse|receptionist|administrator|internship|intern|coordinator|supervisor|lead|head|director|architect|scientist|researcher|therapist|trainer|instructor|advisor|counselor|liaison|broker|operator|representative|clerk|secretary|attorney|lawyer|paralegal|auditor|controller|planner|estimator|inspector|surveyor|forester|agronomist|horticulturist|therapist)\b",
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
    "view details",
    "details",
    "see more",
    "learn more",
    "read more",
    "click here",
    "here",
    "open roles",
    "current openings",
    "job openings",
    "job vacancy",
    "positions",
    "available positions",
    "open positions",
    "join us",
    "join our team",
    "see all jobs",
    "view all jobs",
    "career opportunities",
    "opportunities",
    "job search",
    "search jobs",
    "our open positions",
    "explore our open positions",
    "quick links",
    "quick access",
    "useful links",
    "important links",
    "home",
    "about",
    "about us",
    "why us",
    "why work with us",
    "our team",
    "services",
    "contact",
    "contact us",
    "success stories",
    "get hired",
    "start hiring",
    "hire staff",
    "hire staff",
    "current vacancies",
    "what do we look for in our potential employees?",
    "general delivery conditions",
}


SKIP_DOMAINS = (
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
)

SKIP_URL_PARTS = (
    "/blog",
    "/blogs",
    "/news",
    "/insight",
    "/article",
    "/articles",
    "/category",
    "/categories",
    "/tag",
    "/tags",
    "/portfolio",
    "/gallery",
    "/about",
    "/about-us",
    "/contact",
    "/contact-us",
    "/team",
    "/services",
    "/service/",
    "/faq",
    "/privacy",
    "/terms",
    "/press",
    "/media",
    "/events",
    "/courses",
    "/product",
    "/projects",
    "/wp-content",
    "/wp-json",
)

CAREER_SECTION_TITLES = {
    "open roles",
    "current openings",
    "job openings",
    "job vacancy",
    "job vacancies",
    "careers",
    "career",
    "positions",
    "available positions",
    "open positions",
    "join us",
    "join our team",
    "apply",
    "apply now",
    "see all jobs",
    "view all jobs",
    "vacancies",
    "career opportunities",
    "opportunities",
    "job search",
    "search jobs",
    "our open positions",
    "explore our open positions",
    "available positions",
    "quick links",
    "quick access",
    "useful links",
    "important links",
    "get in touch",
    "follow us",
    "find us",
    "reach us",
    "contact info",
    "company profile",
    "our services",
    "footer",
    "menu",
    "what's open right now",
    "what do we look for in our potential employees?",
    "about us",
    "why work with us",
    "current vacancies",
    "careers at giftmandu",
    "why work with giftmandu",
    "about giftmandu",
}


_robots_cache = {}


def reset_robots_cache():
    _robots_cache.clear()


def get_robots_parser(base_url: str):
    """Fetch and parse robots.txt for a site, once per scrape run.

    Returns a RobotFileParser, or None when no restriction applies
    (unreachable robots.txt, 404, or rate-limited responses). 401/403
    responses are treated as disallow-all.
    """
    if not base_url:
        return None
    try:
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = f"{base}/robots.txt"
    except Exception:
        return None

    key = base.lower()
    if key in _robots_cache:
        return _robots_cache[key]

    parser = None
    try:
        resp = http_requests.get(
            robots_url,
            timeout=ROBOTS_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
        elif resp.status_code in (401, 403):
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
    except Exception:
        parser = None

    _robots_cache[key] = parser
    return parser


def robots_allow(url: str) -> bool:
    """True when robots.txt allows fetching this URL (or can't be fetched)."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return True
    parser = get_robots_parser(base)
    if parser is None:
        return True
    return parser.can_fetch(USER_AGENT, url)


class RateLimitedError(Exception):
    def __init__(self, url="", retry_after=None):
        self.url = url
        self.retry_after = retry_after
        super().__init__(f"Rate limited (429): {url}")


def parse_retry_after(value, default_seconds=3600):
    """Parse a Retry-After header (seconds or HTTP-date) into seconds."""
    if not value:
        return default_seconds
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    try:
        parsed = parsedate_to_datetime(value)
        if parsed:
            seconds = (
                parsed - dt.datetime.now(dt.timezone.utc)
            ).total_seconds()
            return max(int(seconds), 0) or default_seconds
    except Exception:
        pass
    return default_seconds


def safe_page_goto(page, url, retries=2):
    for attempt in range(retries):
        try:
            response = page.goto(
                url,
                timeout=PAGE_TIMEOUT,
                wait_until="domcontentloaded",
            )
            return response
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(1 + random.uniform(0, 1))
    return None


def clean_title(title: str) -> str:
    if not title:
        return ""
    title = re.sub(r"\s+", " ", title)
    title = title.strip()
    return title[:500]


def infer_experience_level(title: str, requirements: str = "", description: str = "") -> str:
    """Infer the experience level of an opening from its scraped text.

    Keyword priority: intern -> junior -> lead -> senior -> mid -> unknown.
    Experience ranges (e.g. "2-5 years") are handled so "5 years" inside a
    range does not accidentally classify as senior.
    """
    text = " ".join(filter(None, [title, requirements, description])).lower()

    intern_patterns = [
        r"\bintern\b",
        r"\binternship",
        r"\btrainee",
    ]
    junior_patterns = [
        r"\bjunior\b",
        r"entry\s*level",
        r"\bfresher",
        r"\bgraduate\b",
        r"0\s*[-–to]+\s*1\s*years?",
        r"[01]\s*[-–to]+\s*\d+\s*years?",
        r"\b1\s*years?",
        r"less than 1 year",
        r"early career",
    ]
    lead_patterns = [
        r"\blead\b",
        r"head of",
        r"\bdirector\b",
    ]
    senior_word_patterns = [
        r"\bsenior\b",
        r"\bsr\.?\b",
        r"\bexpert\b",
    ]
    senior_range_patterns = [
        r"5\s*\+",
        r"6\s*\+",
        r"7\s*\+",
        r"5\s*[-–]+\s*\d+\s*years?",
        r"(?:more than|at least|over|above)\s*5\s*years?",
    ]
    mid_patterns = [
        r"\bmid\b",
        r"mid[- ]level",
        r"[234]\s*\+?\s*years?",
        r"[234]\s*[-–to]+\s*\d+\s*years?",
    ]

    if any(re.search(p, text) for p in intern_patterns):
        return JobOpening.ExperienceLevel.INTERN
    if any(re.search(p, text) for p in junior_patterns):
        return JobOpening.ExperienceLevel.JUNIOR
    if any(re.search(p, text) for p in lead_patterns):
        return JobOpening.ExperienceLevel.LEAD
    if any(re.search(p, text) for p in senior_word_patterns):
        return JobOpening.ExperienceLevel.SENIOR
    if any(re.search(p, text) for p in senior_range_patterns):
        return JobOpening.ExperienceLevel.SENIOR
    if any(re.search(p, text) for p in mid_patterns):
        return JobOpening.ExperienceLevel.MID
    return JobOpening.ExperienceLevel.UNKNOWN


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    return url[:1000]


def ensure_absolute_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def title_from_url(url: str) -> str:
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


def is_listing_page_url(url: str) -> bool:
    """Check if URL points to a career listing page (not a specific job)."""
    if not url:
        return False
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    for career_path in CAREER_PATHS:
        if path == career_path:
            return True
    return False


def strip_query(url: str) -> str:
    """Remove query string from URL for dedup."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def is_job_link(title: str, url: str) -> bool:
    if should_skip_url(url):
        return False

    title_lower = title.lower().strip()

    if title_lower in IGNORE_TITLES:
        return False

    if len(title) < 4:
        return False

    if ROLE_KEYWORD_RE.search(title):
        return True

    if JOB_KEYWORD_RE.search(title):
        return True

    if JOB_KEYWORD_RE.search(url):
        return True

    return False


def page_has_job_signal(page) -> bool:
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
    keywords = ["career", "job", "vacancy", "opening", "apply", "hiring", "join"]
    return any(keyword in text for keyword in keywords)


def fast_find_career_page(website: str):
    """Use requests (no browser) to probe career paths in parallel.

    Returns the first career URL that returns HTTP 200 and contains
    job-related keywords in the HTML source.  Falls back to None.
    """
    if not website:
        return None

    website = ensure_absolute_url(website).rstrip("/")
    try:
        parsed = urlparse(website)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None

    job_keywords = (
        "career", "job", "vacancy", "vacancies", "opening",
        "apply", "hiring", "recruitment", "join us",
    )

    def probe(path):
        url = f"{base}{path}"
        if not robots_allow(url):
            return None
        try:
            time.sleep(random.uniform(0.5, 1.5))
            resp = http_requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=True,
            )
            if resp.status_code == 429:
                raise RateLimitedError(
                    url=url,
                    retry_after=parse_retry_after(
                        resp.headers.get("Retry-After")
                    ),
                )
            if resp.ok and resp.status_code < 400:
                text = resp.text.lower()
                if any(kw in text for kw in job_keywords):
                    return url
        except RateLimitedError:
            raise
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(probe, p): p for p in CAREER_PATHS}
        try:
            for future in as_completed(futures, timeout=30):
                result = future.result()
                if result:
                    # Cancel remaining probes
                    for f in futures:
                        f.cancel()
                    return result
        except Exception:
            # Timeout or other error — cancel everything
            for f in futures:
                f.cancel()

    return None


def resolve_target_url(company):
    """Pick the career listing URL for a company.

    Prefers the cached Company.career_url when a single validation
    request still returns 200 with job keywords; otherwise probes the
    known career paths again.
    """
    if company.career_url:
        try:
            parsed = urlparse(company.career_url)
            if parsed.scheme not in ("http", "https"):
                company.career_url = ""
                company.save(update_fields=["career_url"])
                return fast_find_career_page(company.website)

            resp = http_requests.get(
                company.career_url,
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=True,
            )
            if resp.status_code == 429:
                raise RateLimitedError(
                    url=company.career_url,
                    retry_after=parse_retry_after(
                        resp.headers.get("Retry-After")
                    ),
                )
            job_keywords = (
                "career", "job", "vacancy", "vacancies", "opening",
                "apply", "hiring", "recruitment", "join us",
            )
            if resp.ok and resp.status_code < 400:
                if any(kw in resp.text.lower() for kw in job_keywords):
                    return company.career_url
        except RateLimitedError:
            raise
        except Exception:
            pass

        company.career_url = ""
        company.save(update_fields=["career_url"])

    return fast_find_career_page(company.website)


def find_job_listing_page(page, website: str):
    if not website:
        return None
    website = ensure_absolute_url(website)
    website = website.rstrip("/")
    try:
        parsed = urlparse(website)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        base = website
    for path in CAREER_PATHS:
        url = f"{base}{path}"
        try:
            response = safe_page_goto(page, url)
            if response and response.ok and page_has_job_signal(page):
                return url
        except Exception:
            continue
    return None


def parse_job_blocks(text: str) -> dict:
    """Parse structured fields from a job posting's text.

    Handles both detail page content and card/short descriptions.
    """
    result = {
        "description": "",
        "requirements": "",
        "education": "",
        "location": "",
        "salary": "",
        "deadline": "",
    }
    if not text or len(text.strip()) < 20:
        return result

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > 60000:
        text = text[:60000]

    lines = text.split("\n")

    requirement_headers = {
        "requirement", "requirements", "qualification", "qualifications",
        "skills required", "required skills", "key skills",
        "what we look for", "what are we looking for",
        "job requirement", "job requirements", "job specification",
        "responsibilities", "roles and responsibilities", "key responsibilities",
    }

    # Also accept substrings of these for line-by-line matching
    requirement_substrings = ["requirement", "qualification", "responsibilit"]

    education_headers = {
        "education", "education level", "qualification", "qualifications",
        "academic requirement", "academic qualification", "education requirement",
    }

    location_pattern = re.compile(
        r"(?:job\s*location|workplace|work\s*from|based\s*in|location)\s*[:：\-]\s*(.+)",
        re.IGNORECASE,
    )

    salary_pattern = re.compile(
        r"(?:salary\s*(?:range)?|compensation|pay)\s*[:：\-]\s*(.+)",
        re.IGNORECASE,
    )

    salary_value_re = re.compile(
        r"(?:rs\.?\s*|npr\s*|रु\s*|\$\s*|usd\s*)\d[\d,.\s]*(?:[-–to]+\s*(?:rs\.?\s*|npr\s*|रु\s*|\$\s*|usd\s*)?[\d,.\s]*)?",
        re.IGNORECASE,
    )

    footer_markers = ("©", "copyright", "all rights reserved", "privacy policy",
                      "terms of use", "terms and conditions", "follow us", "newsletter")

    apply_markers = ("apply for this job", "apply now", "how to apply",
                     "interested people", "forward their resume", "send your cv",
                     "submit your application")

    metadata_labels = (
        "functional title", "corporate title", "no. of vacancies",
        "gender", "deadline", "deadline date", "share this job",
        "position currently available",
    )

    title_marker = re.compile(
        r"(?:^|\n)\s*(?:home\s+services\s+product|home|services|products?)\s*$",
        re.IGNORECASE,
    )

    # Find the first known header or job-relevant heading
    first_header_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip().rstrip(":").strip().lower()
        if stripped in requirement_headers or stripped in education_headers:
            first_header_idx = i
            break
        if any(h in stripped for h in requirement_substrings):
            first_header_idx = i
            break

    # Find education
    education_idx = -1
    education_end_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip().rstrip(":").strip().lower()
        if stripped in education_headers or any(h in stripped for h in ["education", "qualification"]):
            education_idx = i
            # Education value is on the same line after ":" or on next lines
            after_colon = re.split(r"[:：]\s*", line.strip(), maxsplit=1)
            if len(after_colon) > 1 and after_colon[1].strip():
                edu_text = after_colon[1].strip()
                # Check if next line continues (non-header, non-empty)
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    next_lower = next_line.lower().rstrip(":").strip()
                    if (next_line and
                        next_lower not in requirement_headers and
                        next_lower not in education_headers and
                        not any(h in next_lower for h in ["level", "salary", "location", "job location", "work hours", "experience", "deadline"])):
                        edu_text = f"{edu_text} {next_line}"
                result["education"] = clean_title(edu_text)[:500]
                education_end_idx = i + 1
            else:
                # Value on next line
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    next_lower = next_line.lower().rstrip(":").strip()
                    if (next_line and
                        next_lower not in requirement_headers and
                        next_lower not in education_headers and
                        not any(h in next_lower for h in ["level", "salary", "location", "job location", "work hours", "experience", "deadline"])):
                        result["education"] = clean_title(next_line)[:500]
                        education_end_idx = i + 2
            break

    # Find requirements
    req_start = -1
    for i, line in enumerate(lines):
        stripped = line.strip().rstrip(":").strip().lower()
        if stripped in requirement_headers or any(h in stripped for h in requirement_substrings):
            req_start = i
            break

    if req_start >= 0:
        req_lines = []
        for idx, line in enumerate(lines[req_start + 1:], start=req_start + 1):
            stripped_lower = line.strip().lower()
            if stripped_lower in footer_markers:
                break
            if any(m in stripped_lower for m in apply_markers):
                break
            if stripped_lower.startswith("share this job"):
                break
            # Stop at another major section header
            check = stripped_lower.rstrip(":").strip()
            if check in education_headers:
                break
            if any(h in check for h in ["benefits", "salary:", "location:", "job location:", "deadline", "how to apply", "apply for"]):
                break
            # Skip duplicate requirement headers (e.g. "Requirements" then "REQUIREMENT")
            if (check in requirement_headers or any(h in check for h in requirement_substrings)):
                prev_check = lines[idx - 1].strip().lower().rstrip(":").strip() if idx > 0 else ""
                if prev_check in requirement_headers or any(h in prev_check for h in requirement_substrings):
                    continue
                break
            if line.strip():
                req_lines.append(line.strip())
        result["requirements"] = clean_title("\n".join(req_lines))[:20000]

    # Find location
    for i, line in enumerate(lines):
        match = location_pattern.search(line)
        if match:
            result["location"] = clean_title(match.group(1))[:500]
            break

    # Find salary
    for i, line in enumerate(lines):
        match = salary_pattern.search(line)
        if match:
            val = match.group(1).strip()
            # Validate it contains at least one digit
            if re.search(r"\d", val):
                result["salary"] = clean_title(val)[:500]
                break
            # Check for standalone salary value on same line
            val_match = salary_value_re.search(line)
            if val_match and re.search(r"\d", val_match.group(0)):
                result["salary"] = clean_title(val_match.group(0))[:500]
                break
            # If it's a short non-empty label, capture it (e.g. "Competitive and negotiable")
            # but only if it doesn't look like a bare currency symbol with no value
            if (val and len(val) < 100
                    and re.search(r"\d|competitive|negotiable|neg|doe|depends|based on experience|per month|per year|per hour| annually", val, re.IGNORECASE)):
                result["salary"] = clean_title(val)[:500]
                break

    # Also check for "rs." + number pattern
    if not result["salary"]:
        for line in lines:
            val_match = salary_value_re.search(line)
            if val_match and re.search(r"\d", val_match.group(0)):
                result["salary"] = clean_title(val_match.group(0))[:500]
                break

    # Find deadline
    deadline_pattern = re.compile(
        r"(?:deadline|deadline\s*date|apply\s*before|last\s*date|last\s*date\s*to\s*apply|applications?\s*(?:close|due)|closing\s*date)\s*[:：\-]?\s*(.+)",
        re.IGNORECASE,
    )
    date_re = re.compile(
        r"\d{1,2}[\s/\-\.](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[\s/\-\.]\d{2,4}|\d{4}[\s/\-\.](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[\s/\-\.]\d{1,2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2},?\s*\d{4}|\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4}",
        re.IGNORECASE,
    )
    for line in lines:
        match = deadline_pattern.search(line)
        if match:
            val = match.group(1).strip()
            date_match = date_re.search(val)
            if date_match:
                result["deadline"] = clean_title(date_match.group(0))[:20]
            elif val and len(val) < 50:
                result["deadline"] = clean_title(val)[:20]
            break
    if not result["deadline"]:
        for line in lines:
            date_match = date_re.search(line)
            if date_match:
                line_lower = line.lower()
                if any(kw in line_lower for kw in ("deadline", "apply before", "last date", "closing", "due date")):
                    result["deadline"] = clean_title(date_match.group(0))[:20]
                    break

    # Description: text before first header, cleaned
    desc_lines = []
    cutoff = first_header_idx if first_header_idx >= 0 else len(lines)
    for line in lines[:cutoff]:
        stripped_lower = line.strip().lower()
        # Skip nav boilerplate at start
        if stripped_lower in title_marker.pattern:
            continue
        if stripped_lower in footer_markers:
            break
        if any(m in stripped_lower for m in apply_markers):
            break
        # Skip metadata lines (Functional Title, Corporate Title, etc.)
        # but keep them if they contain useful info after the colon
        if any(stripped_lower.startswith(ml) for ml in metadata_labels):
            continue
        if line.strip():
            desc_lines.append(line.strip())

    # Filter out empty-only lines that separate sections
    desc_lines = [l for l in desc_lines if l.strip()]
    result["description"] = clean_title("\n".join(desc_lines))[:40000]

    # Also check for inline footer markers (e.g. © on same line as text)
    inline_footer_re = re.compile(
        r"©|copyright|all rights reserved|privacy policy|terms of use",
        re.IGNORECASE,
    )
    if result["description"]:
        match = inline_footer_re.search(result["description"])
        if match:
            result["description"] = clean_title(result["description"][:match.start()])[:40000]

    return result


def extract_job_links(page, source_url: str):
    try:
        if not robots_allow(source_url):
            return []

        response = page.goto(
            source_url,
            timeout=PAGE_TIMEOUT,
            wait_until="domcontentloaded",
        )
        if response and response.status == 429:
            raise RateLimitedError(
                url=source_url,
                retry_after=parse_retry_after(
                    response.headers.get("Retry-After")
                ),
            )
        page.wait_for_timeout(1500)

        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(500)

        raw_links = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || a.title || a.getAttribute('aria-label') || '')
                        .trim()
                        .replace(/\\s+/g, ' '),
                href: a.href,
                inNav: !!a.closest('header, nav, footer, [role="navigation"], [role="banner"], [role="contentinfo"]')
            }))
            """
        )

    except RateLimitedError:
        raise
    except Exception:
        return []

    results = []
    seen_urls = set()
    seen_stripped_urls = set()
    source_lower = normalize_url(source_url).rstrip("/").lower()
    source_stripped = strip_query(source_url).lower()

    for link in raw_links:
        title = clean_title(link.get("text", ""))
        url = normalize_url(link.get("href", ""))
        in_nav = link.get("inNav", False)
        url_lower = url.rstrip("/").lower()

        if should_skip_url(url):
            continue

        if url_lower == source_lower:
            continue

        if any(part in url_lower for part in SKIP_URL_PARTS):
            continue

        # Skip nav/header/footer links entirely
        if in_nav:
            continue

        # Skip listing page links (e.g. "GET HIRED" pointing to /vacancies)
        if is_listing_page_url(url):
            continue

        # Skip query-only duplicates
        stripped = strip_query(url).lower()
        if stripped in seen_stripped_urls:
            continue
        seen_stripped_urls.add(stripped)

        generic_title = (
            re.sub(r"[\(\[].*?[\)\]]", "", title).strip().lower()
        )
        if generic_title in CAREER_SECTION_TITLES:
            continue

        if generic_title in IGNORE_TITLES:
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


def extract_career_section_jobs(page, source_url: str):
    try:
        rows = page.evaluate(
            """
            () => {
                const results = [];
                const bodyText = document.body ? document.body.innerText : '';
                const els = Array.from(
                    document.querySelectorAll(
                        'h1,h2,h3,h4,h5,h6,' +
                        '[class*="job" i],[class*="position" i],' +
                        '[class*="role" i],[class*="vacan" i]'
                    )
                );
                for (const el of els) {
                    // Skip headings inside nav/header/footer
                    if (el.closest('header, nav, footer, [role="navigation"], [role="banner"], [role="contentinfo"]')) {
                        continue;
                    }
                    const direct = (el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ').trim();
                    if (direct.length < 4 || direct.length > 140) continue;

                    // Strategy: search for this heading in the page body text
                    // and extract content from that point forward.
                    // Stop at the next numbered heading (e.g. "6. ...") or
                    // after 2000 chars.
                    let body = '';
                    const idx = bodyText.indexOf(direct);
                    if (idx >= 0) {
                        const afterHeading = bodyText.substring(idx + direct.length);
                        // Find next numbered heading or section boundary
                        const nextHeadingMatch = afterHeading.match(new RegExp('\\n\\s*\\d+\\.\\s+\\w'));
                        const cutPoint = nextHeadingMatch ? nextHeadingMatch.index : 2000;
                        body = direct + '\\n' + afterHeading.substring(0, Math.min(cutPoint, 2000)).trim();
                    }
                    // Fallback: walk up DOM (original approach)
                    if (!body || body.length < 50) {
                        let block = el;
                        for (let depth = 0; depth < 4; depth++) {
                            const parent = block.parentElement;
                            if (!parent) break;
                            if (parent.tagName === 'NAV' || parent.tagName === 'HEADER' ||
                                parent.tagName === 'FOOTER' || parent.tagName === 'BODY' ||
                                parent.getAttribute('role') === 'navigation' ||
                                parent.getAttribute('role') === 'banner' ||
                                parent.getAttribute('role') === 'contentinfo') break;
                            const text = (block.innerText || '').replace(/\\s+/g, ' ');
                            if (text.length > 1500) break;
                            block = parent;
                        }
                        body = (block.innerText || '').trim().slice(0, 5000);
                    }

                    results.push({
                        title: direct,
                        body: body,
                    });
                }
                return results;
            }
            """
        )
    except Exception:
        return []

    results = []
    seen_titles = set()

    for row in rows:
        title = clean_title(row.get("title", ""))
        body = row.get("body", "")

        if len(title) < 4:
            continue

        generic_title = (
            re.sub(r"[\(\[].*?[\)\]]", "", title).strip().lower()
        )

        if generic_title in CAREER_SECTION_TITLES or generic_title in IGNORE_TITLES:
            continue

        # Require title to match a role keyword (prevents nav/section headings)
        if not ROLE_KEYWORD_RE.search(title):
            continue

        if generic_title in seen_titles:
            continue
        seen_titles.add(generic_title)

        # Parse structured fields from card body
        parsed = parse_job_blocks(body)

        results.append(
            {
                "title": title[:500],
                "url": source_url[:1000],
                "source_url": source_url[:1000],
                "description": parsed["description"][:40000],
                "requirements": parsed["requirements"][:20000],
                "education": parsed["education"][:500],
                "location": parsed["location"][:500],
                "salary": parsed["salary"][:500],
                "deadline": parsed["deadline"][:20],
            }
        )

    return results


def extract_job_details(page, job_url: str, current_title: str = ""):
    """Extract structured fields from a job detail page.

    Returns a dict with description, requirements, education, location,
    salary, and a potentially updated title (from page H1).
    """
    details = {
        "title": current_title,
        "description": "",
        "requirements": "",
        "education": "",
        "location": "",
        "salary": "",
        "deadline": "",
    }

    if not job_url:
        return details

    try:
        if not robots_allow(job_url):
            return details

        response = page.goto(
            job_url,
            timeout=PAGE_TIMEOUT,
            wait_until="domcontentloaded",
        )
        if response and response.status == 429:
            raise RateLimitedError(
                url=job_url,
                retry_after=parse_retry_after(
                    response.headers.get("Retry-After")
                ),
            )
        page.wait_for_timeout(1500)

        for _ in range(3):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(300)

        # Strip chrome and get main content text
        body_text = page.evaluate(
            """
            () => {
                // Clone body to avoid modifying live DOM
                const clone = document.body.cloneNode(true);

                // Remove chrome elements
                const removeSelectors = [
                    'header', 'nav', 'footer',
                    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                    'script', 'style', 'iframe', 'noscript',
                    'form', '[class*="apply"]', '[class*="form"]',
                    '[class*="cookie"]', '[class*="popup"]', '[class*="modal"]',
                ];
                for (const sel of removeSelectors) {
                    clone.querySelectorAll(sel).forEach(el => el.remove());
                }

                // Try to find main content area
                let main = clone.querySelector('main, article, [role="main"]');
                if (!main) {
                    // Try common class patterns
                    main = clone.querySelector(
                        '[class*="job" i], [class*="career" i], ' +
                        '[class*="vacanc" i], [class*="post" i], ' +
                        '[class*="article" i], [class*="content" i]'
                    );
                }
                if (!main) main = clone;

                return (main.innerText || '').trim();
            }
            """
        )

    except RateLimitedError:
        raise
    except Exception:
        return details

    if not body_text:
        return details

    body_text = re.sub(r"[ \t]+", " ", body_text)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

    if len(body_text) > 120000:
        body_text = body_text[:120000]

    # Try to get the page H1 and use as title if better
    try:
        h1_text = page.evaluate(
            """
            () => {
                const h1 = document.querySelector('h1');
                return h1 ? (h1.innerText || h1.textContent || '').trim() : '';
            }
            """
        )
        h1_text = clean_title(h1_text)
        if h1_text and len(h1_text) >= 3:
            # Use H1 if current title is boilerplate or H1 is a better role match
            title_lower = current_title.lower().strip()
            if (title_lower in IGNORE_TITLES
                    or title_lower in CAREER_SECTION_TITLES
                    or "view details" in title_lower
                    or len(current_title) < 4):
                details["title"] = h1_text
            elif ROLE_KEYWORD_RE.search(h1_text) and not ROLE_KEYWORD_RE.search(current_title):
                details["title"] = h1_text
    except Exception:
        pass

    parsed = parse_job_blocks(body_text)
    details["description"] = parsed["description"][:40000]
    details["requirements"] = parsed["requirements"][:20000]
    details["education"] = parsed["education"][:500]
    details["location"] = parsed["location"][:500]
    details["salary"] = parsed["salary"][:500]
    details["deadline"] = parsed["deadline"][:20]

    return details


def run_scrape_for_active_companies(limit=None):
    close_old_connections()

    run = ScrapeRun.objects.create(
        status=ScrapeRun.Status.RUNNING
    )

    reset_robots_cache()

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
                close_old_connections()

                if (
                    company.rate_limited_until
                    and company.rate_limited_until > timezone.now()
                ):
                    continue

                processed += 1

                try:
                    # Use the cached career page when it still validates,
                    # otherwise probe career paths with plain HTTP.
                    target_url = resolve_target_url(company)

                    if not target_url:
                        company.last_scraped_at = timezone.now()
                        company.last_error = "No career page found"
                        company.career_url = ""
                        company.save(
                            update_fields=[
                                "last_scraped_at",
                                "last_error",
                                "career_url",
                            ]
                        )
                        continue

                    company.career_url = target_url

                    links = extract_job_links(page, target_url)

                    # Career pages often render roles as heading cards with
                    # no individual links; merge those in.
                    links.extend(
                        extract_career_section_jobs(page, target_url)
                    )

                    company_seen_keys = set()

                    for item in links:
                        jobs_seen += 1

                        job_key = JobOpening.make_key(
                            company.display_name,
                            item["title"],
                            item["url"],
                        )

                        company_seen_keys.add(job_key)

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

                        # If item already has parsed data (career section cards),
                        # use it directly.
                        if item.get("description") or item.get("requirements"):
                            job.title = item["title"]
                            job.description = item.get("description", "")
                            job.requirements = item.get("requirements", "")
                            job.education = item.get("education", "")
                            job.location = item.get("location", "")
                            job.salary = item.get("salary", "")
                            job.deadline = item.get("deadline", "")
                            job.experience_level = infer_experience_level(
                                job.title,
                                job.requirements,
                                job.description,
                            )
                            close_old_connections()
                            job.save()

                            time.sleep(REQUEST_DELAY_SECONDS)
                            continue

                        # Open the job page itself to capture details.
                        details = extract_job_details(
                            page,
                            item["url"],
                            current_title=item["title"],
                        )

                        job.title = details.get("title") or item["title"]
                        job.description = details["description"]
                        job.requirements = details["requirements"]
                        job.education = details["education"]
                        job.location = details["location"]
                        job.salary = details["salary"]
                        job.deadline = details.get("deadline") or ""
                        job.experience_level = infer_experience_level(
                            job.title,
                            job.requirements,
                            job.description,
                        )

                        close_old_connections()
                        job.save()

                        time.sleep(REQUEST_DELAY_SECONDS)

                    # Deactivate openings that were not listed this run,
                    # but only when the site actually had jobs for us.
                    if company_seen_keys:
                        company.openings.filter(
                            is_active=True
                        ).exclude(
                            job_key__in=company_seen_keys
                        ).update(is_active=False)

                    company.last_scraped_at = timezone.now()
                    company.last_error = ""
                    company.rate_limited_until = None
                    company.save(
                        update_fields=[
                            "last_scraped_at",
                            "last_error",
                            "career_url",
                            "rate_limited_until",
                        ]
                    )

                except RateLimitedError as exc:
                    retry_at = timezone.now() + timedelta(
                        seconds=exc.retry_after or 3600
                    )
                    company.last_scraped_at = timezone.now()
                    company.last_error = (
                        f"Rate limited (429), retry after "
                        f"{retry_at:%Y-%m-%d %H:%M}"
                    )
                    company.rate_limited_until = retry_at
                    company.save(
                        update_fields=[
                            "last_scraped_at",
                            "last_error",
                            "rate_limited_until",
                        ]
                    )
                    errors.append(f"{company.name}: rate limited")

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
        close_old_connections()
        run.finished_at = timezone.now()
        run.companies_processed = processed
        run.jobs_seen = jobs_seen
        run.new_jobs = new_jobs
        run.save()

    return run
