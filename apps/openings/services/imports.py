# apps/openings/services/imports.py

import csv
import io
from dataclasses import dataclass, field

from apps.openings.models import Company


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped_duplicate: int = 0
    skipped_no_name: int = 0
    skipped_names: list = field(default_factory=list)


def _normalize_name(name: str) -> str:
    return " ".join(name.split())


def import_companies_from_csv(csv_file):
    """Import companies from a CSV file-like object.

    Dedup rules:
    - Names are normalized (trimmed, whitespace collapsed).
    - Rows with an empty name are skipped.
    - Duplicate names inside the file (case-insensitive): first one wins,
      later rows are skipped and reported.
    - Names already in the database (case-insensitive): the existing row is
      updated with the new company_name/website instead of creating a new one.

    Expected columns: company_name (or name) + website. Other columns are
    ignored. Accepts both text streams (open files, StringIO) and Django
    UploadedFiles (bytes).
    """
    result = ImportResult()

    if hasattr(csv_file, "name"):
        text = csv_file.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
    else:
        reader = csv.DictReader(csv_file)

    fieldnames = reader.fieldnames
    if not fieldnames:
        return result

    if "company_name" in fieldnames:
        name_header = "company_name"
    elif "name" in fieldnames:
        name_header = "name"
    else:
        name_header = fieldnames[0]

    seen = set()

    for row in reader:
        name = _normalize_name(row.get(name_header) or "")
        if not name:
            result.skipped_no_name += 1
            continue

        key = name.casefold()
        if key in seen:
            result.skipped_duplicate += 1
            result.skipped_names.append(name)
            continue
        seen.add(key)

        company_name = (row.get("company_name") or "").strip()
        website = (row.get("website") or "").strip()

        existing = Company.objects.filter(name__iexact=name).first()
        if existing:
            existing.company_name = company_name
            existing.website = website
            existing.save()
            result.updated += 1
        else:
            Company.objects.create(
                name=name,
                company_name=company_name,
                website=website,
            )
            result.created += 1

    return result