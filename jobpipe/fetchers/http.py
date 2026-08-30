"""Shared HTTP behaviour for every fetcher.

Three rules from the spec live here so no individual fetcher has to remember
them:

  * sequential requests with a polite delay — do not hammer
  * exponential backoff on 5xx and connection errors
  * a single company failing must never abort the run
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

USER_AGENT = "jobpipe/0.1 (personal job search; contact via GitHub)"
POLITE_DELAY_SECONDS = 1.0
TIMEOUT_SECONDS = 45
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0
MAX_REDIRECTS = 3

# SSRF defence. Every URL this module fetches is built from a template in a
# fetcher module, so the set of legitimate hosts is closed and known ahead of
# time. requests follows redirects automatically by default, which means a
# compromised or hostile job board can bounce an outbound request to an
# address of its choosing — cloud instance metadata at 169.254.169.254, or
# anything else reachable from this host.
#
# So: redirects are not followed automatically. Each hop is checked against
# this allowlist before it is followed, and at most MAX_REDIRECTS are taken.
ALLOWED_HOSTS = frozenset({
    "boards-api.greenhouse.io",
    "api.ashbyhq.com",
    "api.lever.co",
    "api.smartrecruiters.com",
})


class FetchError(Exception):
    """A company's board could not be fetched. Caught per-company, never fatal."""


class UnsafeURLError(FetchError):
    """A URL or redirect target failed the SSRF checks."""


def _host_is_a_private_ip(host: str) -> bool:
    """True if the host is a literal IP in a range that must never be fetched.

    Hostnames are not resolved here — DNS resolution introduces a
    time-of-check/time-of-use gap, and the host allowlist is the real control.
    This catches the blunt case of a redirect straight to an IP literal.
    """
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return (address.is_private or address.is_loopback or address.is_reserved
            or address.is_link_local or address.is_multicast
            or address.is_unspecified)


def assert_safe_url(url: str) -> None:
    """Raise UnsafeURLError unless the URL is an https call to a known board."""
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise UnsafeURLError(f"refusing non-https URL: {url!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError(f"refusing URL with no host: {url!r}")
    if _host_is_a_private_ip(host):
        raise UnsafeURLError(f"refusing private/loopback address: {host!r}")
    if host not in ALLOWED_HOSTS:
        raise UnsafeURLError(
            f"refusing host not in the ATS allowlist: {host!r}")


# One session per thread, not one shared session.
#
# Greenhouse and Ashby fetch sequentially so a single session was fine.
# SmartRecruiters cannot: its list endpoint carries no description, so each
# posting needs its own detail request, and Canva alone is 248 of them. Those
# run concurrently — and `requests.Session` is explicitly not thread-safe, so
# sharing one across a pool risks connection-pool corruption that would
# surface as sporadic, unreproducible fetch failures.
_local = threading.local()


def _get_session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT,
                                "Accept": "application/json"})
        _local.session = session
    return session


def _get_following_safe_redirects(session: requests.Session, url: str):
    """GET with redirects validated one hop at a time rather than followed blindly."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        response = session.get(current, timeout=TIMEOUT_SECONDS,
                               allow_redirects=False)
        if not response.is_redirect and not response.is_permanent_redirect:
            return response

        target = response.headers.get("Location")
        if not target:
            raise UnsafeURLError(f"redirect from {current!r} with no Location")

        target = requests.compat.urljoin(current, target)
        # A legitimate ATS API does not redirect off its own host, so any
        # cross-host hop is treated as hostile rather than merely unusual.
        assert_safe_url(target)
        log.debug("  following redirect %s -> %s", current, target)
        current = target

    raise UnsafeURLError(f"too many redirects starting at {url!r}")


def get_json(url: str, *, polite_delay: float = POLITE_DELAY_SECONDS):
    """GET a URL and parse JSON, retrying 5xx and 429 with exponential backoff.

    4xx other than 429 is not retried — a 404 means the board token is wrong,
    and retrying it three more times just wastes time on every run.
    """
    session = _get_session()
    last_error: Exception | None = None
    assert_safe_url(url)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = _get_following_safe_redirects(session, url)

            if response.status_code == 404:
                raise FetchError(f"404 (board token likely wrong): {url}")

            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                wait = (float(retry_after) if retry_after and retry_after.isdigit()
                        else BACKOFF_BASE_SECONDS ** attempt)
                last_error = FetchError(
                    f"HTTP {response.status_code} from {url}")
                if attempt < MAX_ATTEMPTS:
                    log.warning("  HTTP %s, retry %d/%d in %.1fs",
                                response.status_code, attempt, MAX_ATTEMPTS, wait)
                    time.sleep(wait)
                    continue
                raise last_error

            response.raise_for_status()
            return response.json()

        except FetchError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                wait = BACKOFF_BASE_SECONDS ** attempt
                log.warning("  %s, retry %d/%d in %.1fs",
                            type(exc).__name__, attempt, MAX_ATTEMPTS, wait)
                time.sleep(wait)
                continue
            raise FetchError(f"{type(exc).__name__} fetching {url}: {exc}") from exc
        finally:
            if polite_delay:
                time.sleep(polite_delay)

    raise FetchError(f"exhausted retries for {url}: {last_error}")
