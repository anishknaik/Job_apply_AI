"""Board token verification — one request per token, no parsing.

The top silent-failure mode in this pipeline is a wrong `board_token`. A bad
token does not raise: Ashby and Greenhouse return an empty list, so the
company simply never appears in the sheet and nothing anywhere says why.

This deliberately does not use the fetchers. It reports the raw HTTP status
and row count so a parsing bug can never be mistaken for a token problem, or
the reverse.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .targets import Target

PROBE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
}

TIMEOUT_SECONDS = 30


def _row_count(ats_type: str, payload) -> int | None:
    """Count rows without interpreting them. None means 'shape unrecognised'."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("jobs", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def probe(target: Target) -> dict:
    """Return {status, rows, note} for one board. Never raises."""
    template = PROBE_URLS.get(target.ats_type)
    if template is None:
        return {"status": "-", "rows": None,
                "note": f"no probe URL for ats_type {target.ats_type!r}"}

    url = template.format(token=target.board_token)
    request = urllib.request.Request(
        url, headers={"User-Agent": "jobpipe-verify/0.1",
                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        note = "token not found" if exc.code == 404 else exc.reason
        return {"status": exc.code, "rows": None, "note": str(note)}
    except Exception as exc:  # noqa: BLE001 - verification must never abort
        return {"status": "ERR", "rows": None,
                "note": f"{type(exc).__name__}: {exc}"}

    try:
        payload = json.loads(body)
    except ValueError:
        return {"status": status, "rows": None, "note": "response was not JSON"}

    rows = _row_count(target.ats_type, payload)
    if rows is None:
        return {"status": status, "rows": None,
                "note": f"unrecognised shape: {type(payload).__name__}"}
    if rows == 0:
        return {"status": status, "rows": 0,
                "note": "EMPTY — wrong token, or genuinely no open roles"}
    return {"status": status, "rows": rows, "note": ""}