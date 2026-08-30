"""Greenhouse job board fetcher.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Verified response shape (2026-08-11):
    {"jobs": [ {...}, ... ], "meta": {"total": N}}

Per-job fields used:
    id              int     stable req id
    title           str
    absolute_url    str     candidate-facing URL
    location.name   str     e.g. "SF, NYC, SEA, CHI" — free text, not a code
    content         str     HTML-*escaped* body ("&lt;h2&gt;..."), needs
                            unescaping AND tag stripping
    first_published str     ISO-8601 — the real posting date
    updated_at      str     ISO-8601 — touched by any edit, so not a post date

Note: `content=true` makes this endpoint large (Stripe alone returns ~4.3 MB).
Postings are yielded one at a time so a run never holds every company's full
payload in memory at once.
"""
from __future__ import annotations

import logging

from ..normalize import Posting, html_to_text, looks_remote
from .http import get_json

log = logging.getLogger(__name__)

ATS_TYPE = "greenhouse"
URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def fetch(company: str, board_token: str):
    """Yield Posting objects for one company. Raises FetchError on failure."""
    payload = get_json(URL.format(token=board_token))
    jobs = payload.get("jobs") or []

    for job in jobs:
        job_id = job.get("id")
        title = (job.get("title") or "").strip()
        url = job.get("absolute_url")
        if job_id is None or not title or not url:
            log.warning("  [%s] skipping malformed job: id=%r title=%r",
                        company, job_id, title)
            continue

        location = (job.get("location") or {}).get("name")
        description = html_to_text(job.get("content"))

        yield Posting(
            ats_type=ATS_TYPE,
            source_id=str(job_id),
            company=company,
            title=title,
            location=location,
            # Greenhouse has no explicit remote flag; infer from location/body.
            remote_flag=looks_remote(location, title, description[:2000]),
            description=description,
            url=url,
            posted_at=job.get("first_published") or job.get("updated_at"),
        )
