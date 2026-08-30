"""SmartRecruiters job board fetcher.

Endpoints (verified live 2026-08-14, against Canva's 248 open postings):

    list   https://api.smartrecruiters.com/v1/companies/{token}/postings
           ?limit=100&offset=N
    detail https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}

This board differs from Greenhouse and Ashby in two ways that shape the whole
module, and both were measured rather than assumed:

  1. **The list carries no description.** It returns title, location, dates
     and taxonomy, and nothing else. Stage A cannot work from that — its
     years-of-experience, sponsorship and description rules all read the body
     — so every posting needs a second request. Canva alone is 248 of them.

  2. **`limit` silently caps at 100.** Asking for 200 or 500 returns exactly
     100 with `totalFound` unchanged, so pagination is mandatory and a naive
     single request would quietly collect the first 100 of 248 and report
     success. Same shape as the wrong-board-token failure: no error, just
     missing data.

Detail requests run concurrently. Measured: 753ms each serially, 95ms
effective across 8 threads, so Canva's full board takes ~24s instead of ~3
minutes. The alternative — pre-screening on title to avoid most detail calls —
was rejected: it would couple this fetcher to filters.yaml, and any later
loosening of a title rule would leave already-stored postings with empty
descriptions that Stage A would then judge on nothing.

Per-posting fields used:
    id                  str   stable req id
    name                str   title
    releasedDate        str   ISO-8601 posting date
    location            obj   {city, region, country, remote, hybrid,
                              fullLocation} — structured, unlike Greenhouse
    postingUrl          str   candidate-facing URL (detail only)
    jobAd.sections      obj   {companyDescription, jobDescription,
                              qualifications, additionalInformation}, each
                              {title, text} with HTML in `text`
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ..normalize import Posting, html_to_text, looks_remote
from .http import FetchError, get_json

log = logging.getLogger(__name__)

ATS_TYPE = "smartrecruiters"
BASE = "https://api.smartrecruiters.com/v1/companies/{token}/postings"

# The API's real ceiling, not a preference. Larger values are accepted and
# silently truncated to this.
PAGE_SIZE = 100

# Concurrency for detail requests. Eight measured at ~10 req/s against this
# API; six keeps a margin below that while still turning three minutes into
# well under one. `get_json`'s own retry/backoff still applies per request.
DETAIL_WORKERS = 6

# The per-request delay in get_json exists to keep sequential fetchers polite.
# Concurrency already spaces these out, so applying it again per call would
# serialise the pool. Lowered rather than removed: the pool still yields
# roughly six requests a second, not a flood.
DETAIL_DELAY_SECONDS = 0.1

# Order matters — this is how the description reads to Stage A and the
# scorer. Qualifications carry the years-of-experience and sponsorship
# language the filters look for, so they must not be dropped.
SECTION_ORDER = ("jobDescription", "qualifications", "additionalInformation",
                 "companyDescription")


def _list_page(token: str, offset: int) -> dict:
    return get_json(f"{BASE.format(token=token)}"
                    f"?limit={PAGE_SIZE}&offset={offset}")


def iter_summaries(company: str, token: str):
    """Every posting summary, following pagination to the end.

    Stops when a page comes back short or the reported total is reached,
    whichever happens first — a board that changes size mid-pagination should
    end the loop rather than spin.
    """
    offset, seen, total = 0, 0, None
    while True:
        payload = _list_page(token, offset)
        page = payload.get("content") or []
        if total is None:
            total = payload.get("totalFound")
            log.info("  [%s] %s posting(s) to page through", company, total)
        if not page:
            return
        for job in page:
            yield job
        seen += len(page)
        if len(page) < PAGE_SIZE or (total is not None and seen >= total):
            return
        offset += PAGE_SIZE


def _description(detail: dict) -> str:
    """Flatten jobAd.sections into text, in reading order."""
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    parts = []
    for name in SECTION_ORDER:
        block = sections.get(name)
        if not isinstance(block, dict):
            continue
        text = html_to_text(block.get("text"))
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _location(job: dict) -> tuple[str | None, bool]:
    """Structured location -> (display string, remote flag).

    Richer than Greenhouse's free text: `remote` is an explicit boolean, so
    it is used directly rather than inferred from wording.
    """
    loc = job.get("location") or {}
    display = loc.get("fullLocation")
    if not display:
        display = ", ".join(
            p for p in (loc.get("city"), loc.get("region"),
                        loc.get("country")) if p) or None
    return display, bool(loc.get("remote"))


def _to_posting(company: str, summary: dict, detail: dict) -> Posting | None:
    job_id = summary.get("id")
    title = (summary.get("name") or "").strip()
    url = detail.get("postingUrl") or detail.get("applyUrl")
    if not job_id or not title or not url:
        log.warning("  [%s] skipping malformed job: id=%r title=%r url=%r",
                    company, job_id, title, url)
        return None

    location, remote = _location(detail if detail.get("location") else summary)
    description = _description(detail)

    return Posting(
        ats_type=ATS_TYPE,
        source_id=str(job_id),
        company=company,
        title=title,
        location=location,
        # The explicit flag when the API sets it; otherwise fall back to the
        # same inference the other fetchers use.
        remote_flag=remote or looks_remote(location, title, description[:2000]),
        description=description,
        url=url,
        posted_at=summary.get("releasedDate"),
    )


def fetch(company: str, board_token: str):
    """Yield Posting objects for one company. Raises FetchError on failure."""
    summaries = list(iter_summaries(company, board_token))
    if not summaries:
        return

    detail_url = BASE.format(token=board_token) + "/{id}"

    def detail(summary):
        try:
            return summary, get_json(
                detail_url.format(id=summary["id"]),
                polite_delay=DETAIL_DELAY_SECONDS)
        except FetchError as exc:
            # One posting failing must not cost the company. The rest of the
            # board is still worth having.
            log.warning("  [%s] detail fetch failed for %s: %s",
                        company, summary.get("id"), exc)
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
