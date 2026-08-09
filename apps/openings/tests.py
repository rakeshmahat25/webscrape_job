# apps/openings/tests.py

import re
from django.test import TestCase

from apps.openings.services.scraper import (
    parse_job_blocks,
    is_job_link,
    is_listing_page_url,
    strip_query,
    should_skip_url,
    IGNORE_TITLES,
    CAREER_SECTION_TITLES,
    ROLE_KEYWORD_RE,
    JOB_KEYWORD_RE,
)


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
