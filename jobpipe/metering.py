"""Revenium metering for the Gemini calls.

Built for the Revenium proof of concept (`revenium-demo-context.md`): every
model call is recorded with what it cost, which posting it was for and which
stage made it, so a dashboard can answer "which stage spends the money" — a
question jobpipe's own token ledger answers only in part. That ledger reads
`candidates_token_count` and never counted thinking tokens, which 2.5 Pro
cannot turn off and Google bills at the output rate.

OFF UNLESS A KEY IS SET. With `REVENIUM_METERING_API_KEY` absent the
middleware is never imported, nothing is patched and no request carries
metadata, so the offline tests and a key-less cron run are byte-identical to
before. Metering never changes a prompt, a model or a score: it observes the
call `providers/` already makes.

Prompt capture stays off — the SDK's default, and deliberately not set here.
The system prompt is the full master profile.

Only the Vertex backend is wired, because it is the one that runs. The
Developer API provider goes through `interactions.create`, a different request
shape, and is left unmetered rather than half-supported.
"""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager

log = logging.getLogger(__name__)

PRODUCT = "jobpipe"

_tags: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "jobpipe_metering_tags", default={})

# Resolved once per process. Re-reading the environment on every call would
# let a test or a late load_dotenv() turn metering on halfway through a run,
# leaving the dashboard with a run that starts in the middle.
_enabled: bool | None = None


def enabled() -> bool:
    """Whether calls are being metered. Activates the middleware on first use.

    Must run before `from google import genai`, which is why the Vertex
    provider calls it first thing in `__init__`.
    """
    global _enabled
    if _enabled is None:
        _enabled = _activate()
    return _enabled


def _activate() -> bool:
    if not (os.environ.get("REVENIUM_METERING_API_KEY") or "").strip():
        return False
    try:
        import revenium_middleware.google  # noqa: F401 - patches google.genai
    except ImportError:
        # A key with no SDK is a setup mistake, but not one worth failing a
        # scoring run over: the run is the product, metering is the view of it.
        log.warning("REVENIUM_METERING_API_KEY is set but revenium-python-sdk "
                    "is not installed; calls will not be metered. "
                    'pip install "revenium-python-sdk[google-genai]"')
        return False
    log.info("Revenium metering on (environment=%s)", _environment())
    return True


def _environment() -> str:
    return os.environ.get("JOBPIPE_METERING_ENV") or "production"


@contextmanager
def tagged(**fields):
    """Attach fields to every model call made inside the block.

    Nested blocks add to the outer ones and win on a clash, so a posting tag
    set by a caller survives the stage tag set by the function it calls.
    Empty values are dropped rather than sent as blanks.
    """
    merged = {**_tags.get(),
              **{k: v for k, v in fields.items() if v not in (None, "")}}
    token = _tags.set(merged)
    try:
        yield
    finally:
        _tags.reset(token)


def for_posting(row):
    """The posting a call is for: its id as the trace, its company as the
    customer. "Cost per target company" is a group-by on this."""
    return tagged(trace_id=str(row["id"]), organizationName=row["company"])


def usage_metadata() -> dict:
    """What the Vertex provider passes as `usage_metadata=` on a metered call."""
    return {"productName": PRODUCT, "environment": _environment(),
            **_tags.get()}


def reset_for_tests() -> None:
    global _enabled
    _enabled = None
