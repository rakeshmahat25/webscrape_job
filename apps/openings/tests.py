# apps/openings/tests.py

import io
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.robotparser import RobotFileParser

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.openings.models import Company, JobOpening, ScrapeRun
from apps.openings.services.imports import import_companies_from_csv
from apps.openings.services.scraper import (
    parse_job_blocks,
    is_job_link,
    is_listing_page_url,
    strip_query,
    should_skip_url,
    infer_experience_level,
    reset_robots_cache,
    robots_allow,
    parse_retry_after,
    RateLimitedError,
    fast_find_career_page,
    run_scrape_for_active_companies,
    IGNORE_TITLES,
    CAREER_SECTION_TITLES,
    ROLE_KEYWORD_RE,
    JOB_KEYWORD_RE,
)


class ImportCompaniesViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            "owner", password="pass12345", is_staff=True
        )
        self.user = User.objects.create_user(
            "normal", password="pass12345"
        )

    def test_anonymous_redirects_to_login(self):
        resp = self.client.get("/import-companies/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_non_staff_forbidden(self):
        self.client.login(username="normal", password="pass12345")
        resp = self.client.get("/import-companies/")
        self.assertEqual(resp.status_code, 403)

    def test_staff_can_upload_csv(self):
        self.client.login(username="owner", password="pass12345")
        csv_text = (
            "sn,company_name,website,Status\n"
            "1,NewCo,https://newco.com,CANDIDATE\n"
        )
        resp = self.client.post(
            "/import-companies/",
            {
                "csv_file": SimpleUploadedFile(
                    "companies.csv",
                    csv_text.encode("utf-8-sig"),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Company.objects.filter(name="NewCo").exists())
        self.assertContains(resp, "Created")
        self.assertContains(resp, "1")

    def test_staff_upload_dedups_against_db(self):
        Company.objects.create(
            name="Existing Co", website="https://old.com"
        )
        self.client.login(username="owner", password="pass12345")
        csv_text = "sn,company_name,website\n1,Existing Co,https://new.com\n"
        resp = self.client.post(
            "/import-companies/",
            {
                "csv_file": SimpleUploadedFile(
                    "companies.csv",
                    csv_text.encode("utf-8-sig"),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Company.objects.filter(name="Existing Co").count(), 1)
        self.assertEqual(
            Company.objects.get(name="Existing Co").website, "https://new.com"
        )

    def test_staff_rejects_non_csv(self):
        self.client.login(username="owner", password="pass12345")
        resp = self.client.post(
            "/import-companies/",
            {
                "csv_file": SimpleUploadedFile(
                    "companies.txt",
                    b"hello",
                    content_type="text/plain",
                )
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Only .csv files are allowed")
        self.assertEqual(Company.objects.count(), 0)

    def test_staff_upload_without_file(self):
        self.client.login(username="owner", password="pass12345")
        resp = self.client.post("/import-companies/", {})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please choose a CSV file")


class LoginRequiredTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="pass12345")

    def test_dashboard_redirects_anonymous(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_company_list_redirects_anonymous(self):
        resp = self.client.get("/companies/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_search_redirects_anonymous(self):
        resp = self.client.get("/search/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_latest_openings_redirects_anonymous(self):
        resp = self.client.get("/api/openings/latest/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_run_scrape_redirects_anonymous(self):
        resp = self.client.post("/run-scrape/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_login_page_renders(self):
        resp = self.client.get("/accounts/login/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sign in")

    def test_authenticated_user_reaches_dashboard(self):
        self.client.login(username="tester", password="pass12345")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Job Monitor")


class JobSearchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="pass12345")
        self.client.login(username="tester", password="pass12345")
        self.company = Company.objects.create(
            name="Acme",
            company_name="Acme Pvt. Ltd.",
            website="https://acme.com",
        )
        self.junior = JobOpening.objects.create(
            company=self.company,
            job_key="junior-key",
            title="Junior Python Developer",
            requirements="Entry level",
            experience_level=JobOpening.ExperienceLevel.JUNIOR,
        )
        self.intern = JobOpening.objects.create(
            company=self.company,
            job_key="intern-key",
            title="Marketing Intern",
            requirements="Internship",
            experience_level=JobOpening.ExperienceLevel.INTERN,
        )
        self.senior = JobOpening.objects.create(
            company=self.company,
            job_key="senior-key",
            title="Senior QA Engineer",
            requirements="5+ years experience",
            experience_level=JobOpening.ExperienceLevel.SENIOR,
        )

    def test_keyword_matches_title(self):
        resp = self.client.get("/search/", {"q": "Python"})
        self.assertContains(resp, "Junior Python Developer")
        self.assertNotContains(resp, "Marketing Intern")

    def test_keyword_matches_requirements(self):
        resp = self.client.get("/search/", {"q": "Internship"})
        self.assertContains(resp, "Marketing Intern")

    def test_keyword_matches_company_name(self):
        resp = self.client.get("/search/", {"q": "Acme"})
        self.assertEqual(resp.context["total_results"], 3)

    def test_level_filter(self):
        resp = self.client.get("/search/", {"level": "intern"})
        self.assertContains(resp, "Marketing Intern")
        self.assertNotContains(resp, "Junior Python Developer")
        self.assertNotContains(resp, "Senior QA Engineer")

    def test_combined_query_and_level(self):
        resp = self.client.get("/search/", {"q": "Senior", "level": "senior"})
        self.assertContains(resp, "Senior QA Engineer")
        self.assertEqual(resp.context["total_results"], 1)

    def test_no_results(self):
        resp = self.client.get("/search/", {"q": "zzzzz"})
        self.assertEqual(resp.context["total_results"], 0)
        self.assertContains(resp, "No openings match")

    def test_inactive_jobs_excluded(self):
        self.senior.is_active = False
        self.senior.save()
        resp = self.client.get("/search/", {"q": "QA"})
        self.assertEqual(resp.context["total_results"], 0)


class InferExperienceLevelTest(TestCase):
    def test_intern(self):
        self.assertEqual(
            infer_experience_level("Marketing Intern"),
            JobOpening.ExperienceLevel.INTERN,
        )

    def test_junior(self):
        self.assertEqual(
            infer_experience_level("Python Developer", "Entry level"),
            JobOpening.ExperienceLevel.JUNIOR,
        )

    def test_lead(self):
        self.assertEqual(
            infer_experience_level("Lead Engineer"),
            JobOpening.ExperienceLevel.LEAD,
        )

    def test_senior(self):
        self.assertEqual(
            infer_experience_level("Senior QA Engineer", "5+ years"),
            JobOpening.ExperienceLevel.SENIOR,
        )

    def test_mid(self):
        self.assertEqual(
            infer_experience_level("Accountant", "2-5 years experience"),
            JobOpening.ExperienceLevel.MID,
        )

    def test_senior_beats_mid(self):
        self.assertEqual(
            infer_experience_level("Senior Engineer", "3+ years"),
            JobOpening.ExperienceLevel.SENIOR,
        )

    def test_senior_range(self):
        self.assertEqual(
            infer_experience_level("Engineer", "5-7 years experience"),
            JobOpening.ExperienceLevel.SENIOR,
        )

    def test_unknown(self):
        self.assertEqual(
            infer_experience_level("Support Officer"),
            JobOpening.ExperienceLevel.UNKNOWN,
        )


class ImportCompaniesCsvTest(TestCase):
    def test_import_dedup_and_counts(self):
        Company.objects.create(
            name="Existing Co",
            company_name="Old Name",
            website="https://old.com",
        )

        csv_text = (
            "sn,company_name,website,Status\n"
            "1,Existing Co,https://a.com,CANDIDATE\n"
            "2,existing co,https://b.com,CANDIDATE\n"
            "3,New Co,https://c.com,CANDIDATE\n"
            "4,New Co,https://d.com,CANDIDATE\n"
            "5,,https://e.com,CANDIDATE\n"
        )

        result = import_companies_from_csv(io.StringIO(csv_text))

        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped_duplicate, 2)
        self.assertEqual(result.skipped_no_name, 1)
        self.assertIn("New Co", result.skipped_names)
        self.assertIn("existing co", result.skipped_names)

        self.assertEqual(Company.objects.filter(name__iexact="Existing Co").count(), 1)
        existing = Company.objects.get(name="Existing Co")
        self.assertEqual(existing.website, "https://a.com")
        self.assertEqual(existing.company_name, "Existing Co")

        self.assertEqual(Company.objects.filter(name="New Co").count(), 1)

    def test_import_empty_file(self):
        result = import_companies_from_csv(io.StringIO(""))
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 0)


class IsJobLinkTest(TestCase):
    def test_rejects_nav_titles(self):
        for title in ["Home", "About Us", "Why Us", "Hire Staff",
                       "Get Hired", "Start Hiring", "Success Stories"]:
            self.assertFalse(is_job_link(title, "https://example.com/some-page"))

    def test_rejects_boilerplate_titles(self):
        for title in IGNORE_TITLES:
            self.assertFalse(is_job_link(title, "https://example.com/job/1"))

    def test_accepts_role_titles(self):
        for title in ["Support Officer", "Marketing Manager",
                       "Python Developer", "Sales Assistant",
                       "Junior Graphics Designer (Male/Female)",
                       "8. Store Manager/Storekeeper (Female)",
                       "2. E-commerce Product Listing Manager"]:
            self.assertTrue(is_job_link(title, "https://example.com/career-details/1"))

    def test_accepts_job_keyword_in_url(self):
        self.assertTrue(is_job_link("Senior Python", "https://example.com/job/python-developer"))

    def test_rejects_short_titles(self):
        self.assertFalse(is_job_link("Hi", "https://example.com/job/python-developer"))

    def test_rejects_skip_urls(self):
        self.assertFalse(is_job_link("Python Developer", "https://facebook.com/jobs/1"))
        self.assertFalse(is_job_link("Python Developer", "mailto:test@example.com"))


class IsListingPageUrlTest(TestCase):
    def test_listing_pages(self):
        for url in ["https://example.com/careers", "https://example.com/career",
                     "https://example.com/jobs", "https://example.com/vacancies"]:
            self.assertTrue(is_listing_page_url(url))

    def test_non_listing_pages(self):
        for url in ["https://example.com/career/python-developer",
                     "https://example.com/jobs/123",
                     "https://example.com/about"]:
            self.assertFalse(is_listing_page_url(url))


class StripQueryTest(TestCase):
    def test_strips_query(self):
        self.assertEqual(strip_query("https://example.com/career?setCurrencyId=1"),
                         "https://example.com/career")
        self.assertEqual(strip_query("https://example.com/careers"),
                         "https://example.com/careers")


class ShouldSkipUrlTest(TestCase):
    def test_skip_social(self):
        self.assertTrue(should_skip_url("https://facebook.com/page"))
        self.assertTrue(should_skip_url("https://linkedin.com/in/user"))

    def test_skip_mailto(self):
        self.assertTrue(should_skip_url("mailto:test@example.com"))

    def test_allow_normal(self):
        self.assertFalse(should_skip_url("https://example.com/careers"))


class ParseJobBlocksTest(TestCase):
    def test_giftmandu_card_format(self):
        text = """1. Customer Support Representative (Male/Female)
Position currently available = 1
Level: Entry level
Education: Bachelor's Degree
Job Location: Khumaltar, Dholahity, Lalitpur
Work hours: 10 am - 5 pm
Salary: Competitive and negotiable

We are looking for a Customer Support Representative to join our team. Responsibilities include handling customer inquiries and providing excellent service."""
        result = parse_job_blocks(text)
        self.assertIn("Bachelor's Degree", result["education"])
        self.assertIn("Khumaltar", result["location"])
        self.assertIn("Competitive and negotiable", result["salary"])
        self.assertIn("Customer Support Representative", result["description"])

    def test_globaltech_detail_format(self):
        text = """career
Support Officer
Functional Title: Support Team
Corporate Title: Support
No. of Vacancies: 5
Gender: male
Deadline date: 2024-12-10
Share This Job

We are hiring a Support Officer to join our team.

Requirements
REQUIREMENT
Knowledge of computer
Knows Account Rules
Experienced from Software Company will get more priority.

Benefits
Dynamic and collaborative work environment with talented peers.
Competitive salary
Opportunities for professional development and growth.
Free daily lunches provided in-office for all team members

Interested people can forward their resume to career@globaltechnepal.com"""
        result = parse_job_blocks(text)
        self.assertIn("Knowledge of computer", result["requirements"])
        self.assertIn("Knows Account Rules", result["requirements"])
        self.assertIn("We are hiring a Support Officer", result["description"])
        self.assertNotIn("Home Services Product", result["description"])

    def test_location_from_labeled_line(self):
        text = "Job Location: Lalitpur, Nepal\nSome description text here."
        result = parse_job_blocks(text)
        self.assertIn("Lalitpur", result["location"])

    def test_salary_requires_digits(self):
        text = "Salary: rs."
        result = parse_job_blocks(text)
        # "rs." alone should NOT be captured as salary
        self.assertEqual(result["salary"], "")

    def test_salary_competitive(self):
        text = "Salary: Competitive and negotiable"
        result = parse_job_blocks(text)
        self.assertIn("Competitive and negotiable", result["salary"])

    def test_salary_with_digits(self):
        text = "Salary: Rs. 50,000 - 80,000 per month"
        result = parse_job_blocks(text)
        self.assertIn("50,000", result["salary"])

    def test_empty_text(self):
        result = parse_job_blocks("")
        self.assertEqual(result["description"], "")
        self.assertEqual(result["requirements"], "")

    def test_description_cuts_at_footer(self):
        text = "We are hiring a developer.\n\n© 2024 Company Name. All Rights Reserved."
        result = parse_job_blocks(text)
        self.assertIn("hiring a developer", result["description"])
        self.assertNotIn("©", result["description"])

    def test_description_cuts_at_apply(self):
        text = "We are hiring.\n\nApply Now\nName Email Mobile"
        result = parse_job_blocks(text)
        self.assertIn("hiring", result["description"])
        self.assertNotIn("Apply Now", result["description"])
        self.assertNotIn("Name Email", result["description"])


class RoleKeywordTest(TestCase):
    def test_role_keywords_match(self):
        for word in ["developer", "engineer", "manager", "officer",
                      "designer", "analyst", "consultant"]:
            self.assertTrue(
                ROLE_KEYWORD_RE.search(word),
                f"Expected '{word}' to match ROLE_KEYWORD_RE"
            )

    def test_nav_words_dont_match_role(self):
        for word in ["home", "about", "services", "contact", "why us"]:
            self.assertIsNone(
                ROLE_KEYWORD_RE.search(word),
                f"Expected '{word}' to NOT match ROLE_KEYWORD_RE"
            )


class JobKeywordTest(TestCase):
    def test_job_keywords_match(self):
        for word in ["job", "vacancy", "vacancies", "opening", "hiring",
                      "employment", "recruitment", "staff"]:
            self.assertTrue(
                JOB_KEYWORD_RE.search(word),
                f"Expected '{word}' to match JOB_KEYWORD_RE"
            )

    def test_care_word_does_not_match(self):
        """'care' alone should NOT match (was causing product category false positives)."""
        self.assertIsNone(JOB_KEYWORD_RE.search("care"))
        self.assertIsNone(JOB_KEYWORD_RE.search("Men's Care"))


class RobotsAllowTest(TestCase):
    def setUp(self):
        reset_robots_cache()

    @staticmethod
    def _parser(text):
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser

    @patch("apps.openings.services.scraper.get_robots_parser")
    def test_disallowed_path_blocked(self, mock_get):
        mock_get.return_value = self._parser(
            "User-agent: *\nDisallow: /careers"
        )
        self.assertFalse(robots_allow("https://example.com/careers"))
        self.assertTrue(robots_allow("https://example.com/about"))

    @patch("apps.openings.services.scraper.get_robots_parser")
    def test_no_parser_allows(self, mock_get):
        mock_get.return_value = None
        self.assertTrue(robots_allow("https://example.com/careers"))

    @patch("apps.openings.services.scraper.get_robots_parser")
    def test_deny_all_blocks(self, mock_get):
        mock_get.return_value = self._parser(
            "User-agent: *\nDisallow: /"
        )
        self.assertFalse(robots_allow("https://example.com/anything"))


class ParseRetryAfterTest(TestCase):
    def test_seconds(self):
        self.assertEqual(parse_retry_after("300"), 300)

    def test_http_date(self):
        self.assertGreater(
            parse_retry_after("Wed, 21 Oct 2037 07:28:00 GMT"), 0
        )

    def test_invalid_or_missing(self):
        self.assertEqual(parse_retry_after("garbage"), 3600)
        self.assertEqual(parse_retry_after(""), 3600)
        self.assertEqual(parse_retry_after(None), 3600)


class FastFindCareerPageTest(TestCase):
    def test_probe_uses_three_workers(self):
        with patch("apps.openings.services.scraper.ThreadPoolExecutor") as mock_exec:
            mock_exec.return_value.__enter__.return_value = MagicMock()
            with patch(
                "apps.openings.services.scraper.http_requests.get"
            ) as mock_get:
                mock_get.return_value = SimpleNamespace(
                    status_code=200,
                    ok=True,
                    url="https://example.com/",
                    text="",
                    headers={},
                )
                fast_find_career_page("https://example.com")
        self.assertEqual(mock_exec.call_args.kwargs["max_workers"], 3)


class ScrapeRunEthicsTest(TestCase):
    def setUp(self):
        reset_robots_cache()

    @staticmethod
    def _pw(mock_sync):
        mock_sync.return_value.__enter__.return_value = MagicMock()

    @staticmethod
    def _http(status_code=200, text=""):
        return SimpleNamespace(
            status_code=status_code,
            ok=200 <= status_code < 400,
            url="https://example.com/",
            text=text,
            headers={},
        )

    @patch("apps.openings.services.scraper.sync_playwright")
    @patch("apps.openings.services.scraper.resolve_target_url")
    @patch("apps.openings.services.scraper.close_old_connections")
    @patch("apps.openings.services.scraper.http_requests.get")
    def test_429_marks_company_rate_limited(
        self, mock_get, mock_close, mock_resolve, mock_pw
    ):
        self._pw(mock_pw)
        mock_get.return_value = self._http()
        mock_resolve.side_effect = RateLimitedError(
            url="https://rateco.com/careers", retry_after=300
        )
        company = Company.objects.create(
            name="RateCo", website="https://rateco.com"
        )
        run = run_scrape_for_active_companies(limit=1)
        company.refresh_from_db()
        self.assertIsNotNone(company.rate_limited_until)
        self.assertIn("Rate limited", company.last_error)
        self.assertEqual(run.status, ScrapeRun.Status.SUCCESS)

    @patch("apps.openings.services.scraper.sync_playwright")
    @patch("apps.openings.services.scraper.fast_find_career_page")
    @patch("apps.openings.services.scraper.close_old_connections")
    @patch("apps.openings.services.scraper.http_requests.get")
    def test_career_url_cached_and_reused(
        self, mock_get, mock_close, mock_fast, mock_pw
    ):
        self._pw(mock_pw)
        mock_get.return_value = self._http(
            text="careers job openings at example"
        )
        mock_fast.return_value = "https://example.com/careers"
        company = Company.objects.create(
            name="CacheCo", website="https://example.com"
        )
        run_scrape_for_active_companies(limit=1)
        company.refresh_from_db()
        self.assertEqual(company.career_url, "https://example.com/careers")

        run_scrape_for_active_companies(limit=1)
        mock_fast.assert_called_once_with("https://example.com")

    @patch("apps.openings.services.scraper.sync_playwright")
    @patch("apps.openings.services.scraper.close_old_connections")
    @patch("apps.openings.services.scraper.http_requests.get")
    def test_rate_limited_company_skipped(self, mock_get, mock_close, mock_pw):
        self._pw(mock_pw)
        mock_get.return_value = self._http()
        company = Company.objects.create(
            name="SkipCo",
            website="https://skipco.com",
            rate_limited_until=timezone.now() + timedelta(hours=2),
        )
        run = run_scrape_for_active_companies(limit=1)
        company.refresh_from_db()
        self.assertEqual(run.companies_processed, 0)
        self.assertEqual(company.last_error, "")
