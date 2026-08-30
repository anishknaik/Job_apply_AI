"""Ashby job board fetcher.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{token}

Verified response shape (2026-08-11):
    {"jobs": [ {...}, ... ], "apiVersion": "1"}

Ashby is the friendliest of the four boards: it ships a real `isRemote`
boolean and a `descriptionPlain` field, so neither remote status nor the body
text has to be inferred.

The one trap is `isListed` — unlisted postings are still present in the array
and must be dropped, or the pipeline scores roles that aren't actually open.

Built ahead of its position in the spec's build order because the verification
pass showed most of the target list uses Ashby; deferring it would have left
two-thirds of the companies unfetched.
"""
from __future__ import annotations

import logging

from ..normalize import Posting, html_to_text
from .http import get_json

log = logging.getLogger(__name__)

ATS_TYPE = "ashby"
URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch(company: str, board_token: str):
    """Yield Posting objects for one company. Raises FetchError on failure."""
    payload = get_json(URL.format(token=board_token))
    jobs = payload.get("jobs") or []

    for job in jobs:
        if not job.get("isListed", True):
            continue

        job_id = job.get("id")
        title = (job.get("title") or "").strip()
        url = job.get("jobUrl") or job.get("applyUrl")
        if not job_id or not title or not url:
            log.warning("  [%s] skipping malformed job: id=%r title=%r",
                        company, job_id, title)
            continue

        location = job.get("location")
        secondary = [s.get("location") for s in (job.get("secondaryLocations") or [])
                     if isinstance(s, dict) and s.get("location")]
        if secondary:
            location = "; ".join([location] + secondary) if location \
                else "; ".join(secondary)

        description = job.get("descriptionPlain") \
            or html_to_text(job.get("descriptionHtml"))

        yield Posting(
            ats_type=ATS_TYPE,
            source_id=str(job_id),
            company=company,
            title=title,
            location=location,
            remote_flag=bool(job.get("isRemote")),
            description=description,
            url=url,
            posted_at=job.get("publishedAt"),
        )
