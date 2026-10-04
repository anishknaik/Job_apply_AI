"""Fetcher registry.

Every fetcher exposes the same callable:

    fetch(company: str, board_token: str) -> Iterator[Posting]

and raises FetchError on failure. Adding an ATS means writing that one
function and adding a line to FETCHERS below.

SmartRecruiters needs a second request per posting — its list endpoint has
no description — so it is the one fetcher that fans out concurrently. See its
module docstring for the measurements behind that. Workday is the same shape,
and the one board whose host differs per company - see its docstring.

Lever is deliberately absent. The final target list is 16 Greenhouse and 20
Ashby boards and zero Lever, so a Lever fetcher would be dead code running
every morning. Its verified response shape is recorded in the README if a
Lever company ever joins the list: bare JSON array, title in `text`,
`createdAt` in epoch milliseconds, body split across four `*Plain` fields.
"""
from . import ashby, greenhouse, smartrecruiters, workday
from .http import FetchError

FETCHERS = {
    greenhouse.ATS_TYPE: greenhouse.fetch,
    ashby.ATS_TYPE: ashby.fetch,
    smartrecruiters.ATS_TYPE: smartrecruiters.fetch,
    workday.ATS_TYPE: workday.fetch,
}

SUPPORTED = sorted(FETCHERS)

__all__ = ["FETCHERS", "SUPPORTED", "FetchError"]
