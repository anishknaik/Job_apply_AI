"""Workday job board fetcher.

Endpoints (verified live 2026-10-03, against Barclays' 763 and Broadridge's
284 open postings):

    list   POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
           {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}
    detail GET  https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

The same JSON the company's own careers page loads. `board_token` carries the
three parts a URL needs, as `tenant/wd/site`:

    barclays/wd3/External_Career_Site_Barclays
    broadridge/wd5/Careers

Four things about this board were measured rather than assumed:

  1. **Every tenant has its own host**, unlike the other three. The SSRF
     allowlist in `http` is a closed set, so a new Workday company needs its
     host added there as well as a row in targets.csv. A host that is not
     listed is refused, loudly.

  2. **The list carries no description**, like SmartRecruiters, so every
     posting needs a second request. They run concurrently for the same
     reason and with the same limits.

  3. **`limit` is capped at 20**, and a larger value is a 400 rather than a
     silent truncation.

  4. **`total` is only trustworthy on the first page.** Broadridge reports 284
     at offset 0 and 0 at every later offset, with a full page of results
     beside it. Barclays repeats the real total. So the total is read once,
     and an empty page is what ends the loop.

A list query returns at most 2,000 results however many exist. Neither board
here is near it; a tenant that is (Accenture lists 36,099 roles in India
alone) cannot be fetched whole this way, and the fetcher says so rather than
collecting the first 2,000 and reporting success.

Per-posting fields used, all from the detail's `jobPostingInfo`:
    jobPostingId        str   unique per posting; one requisition posted to
                              several sites gets several (JR-..., JR-...-6)
    title               str
    jobDescription      str   HTML
    location            str   "Newark, NJ" - city and building, no country
    additionalLocations list  present only on multi-location postings
    country.descriptor  str   "India" - appended to the location, because
                              Stage A's location rules cannot place
                              "Hyderabad-Hi-Tec City" or "Whippany, NJ" alone
    startDate           str   ISO date the posting went up
    externalUrl         str   candidate-facing URL
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ..normalize import Posting, html_to_text, looks_remote
from .http import FetchError, get_json

log = logging.getLogger(__name__)

ATS_TYPE = "workday"
BASE = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

# The API's real ceiling. 21 is rejected outright.
PAGE_SIZE = 20

# What one list query can return, whatever the board holds.
RESULT_CAP = 2000

# Sequential, so a small delay per page is the whole politeness budget:
# Barclays is 39 pages.
LIST_DELAY_SECONDS = 0.3

# As in smartrecruiters: the pool spaces requests out already.
DETAIL_WORKERS = 6
DETAIL_DELAY_SECONDS = 0.1


def base_url(board_token: str) -> str:
    """`tenant/wd/site` -> the board's API root. Raises FetchError if malformed."""
    parts = [part.strip() for part in board_token.split("/")]
    if len(parts) != 3 or not all(parts):
        raise FetchError(
            f"workday board_token must be tenant/wd/site, e.g. "
            f"broadridge/wd5/Careers - got {board_token!r}")
    tenant, wd, site = parts
    return BASE.format(tenant=tenant.lower(), wd=wd.lower(), site=site)


def iter_summaries(company: str, base: str):
    """Every posting summary, following pagination to the end."""
    offset, total = 0, None
    while True:
        payload = get_json(
            f"{base}/jobs",
            body={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset,
                  "searchText": ""},
            polite_delay=LIST_DELAY_SECONDS)
        page = payload.get("jobPostings") or []
        if total is None:
            total = payload.get("total") or 0
            log.info("  [%s] %s posting(s) to page through", company, total)
            if total > RESULT_CAP:
                raise FetchError(
                    f"board lists {total} postings and a query returns at "
                    f"most {RESULT_CAP}; it cannot be fetched whole")
        if not page:
            return
        for job in page:
            yield job
        offset += len(page)
        if len(page) < PAGE_SIZE or offset >= total:
            return


def _location(info: dict) -> str | None:
    """Every location the posting names, the first with its country."""
    primary = (info.get("location") or "").strip()
    country = ((info.get("country") or {}).get("descriptor") or "").strip()
    first = ", ".join(part for part in (primary, country) if part)
    extra = [str(loc).strip() for loc in info.get("additionalLocations") or []
             if str(loc).strip()]
    return "; ".join([first] + extra if first else extra) or None


def _to_posting(company: str, summary: dict, detail: dict) -> Posting | None:
    info = detail.get("jobPostingInfo") or {}
    job_id = info.get("jobPostingId")
    title = (info.get("title") or summary.get("title") or "").strip()
    url = info.get("externalUrl")
    if not job_id or not title or not url:
        log.warning("  [%s] skipping malformed job: id=%r title=%r url=%r",
                    company, job_id, title, url)
        return None

    location = _location(info)
    description = html_to_text(info.get("jobDescription"))

    return Posting(
        ats_type=ATS_TYPE,
        source_id=str(job_id),
        company=company,
        title=title,
        location=location,
        remote_flag=looks_remote(location, title, description[:2000]),
        description=description,
        url=url,
        posted_at=info.get("startDate"),
    )


def fetch(company: str, board_token: str):
    """Yield Posting objects for one company. Raises FetchError on failure."""
    base = base_url(board_token)
    summaries = [s for s in iter_summaries(company, base)
                 if s.get("externalPath")]
    if not summaries:
        return

    def detail(summary):
        try:
            return summary, get_json(base + summary["externalPath"],
                                     polite_delay=DETAIL_DELAY_SECONDS)
        except FetchError as exc:
            # One posting failing must not cost the company.
            log.warning("  [%s] detail fetch failed for %s: %s",
                        company, summary.get("externalPath"), exc)
            return summary, None

    failures = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        for summary, body in pool.map(detail, summaries):
            if body is None:
                failures += 1
                continue
            posting = _to_posting(company, summary, body)
            if posting is not None:
                yield posting

    if failures:
        log.warning("  [%s] %d of %d detail fetches failed",
                    company, failures, len(summaries))
