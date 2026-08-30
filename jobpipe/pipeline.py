"""Pipeline stages, wired together.

Partial failure is normal: one company's endpoint returning garbage must never
abort a run. Every per-company failure is caught, logged, and surfaced in the
run summary rather than raised.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import db, dedupe, filters
from .db import get_unscored
from .fetchers import FETCHERS, FetchError
from .targets import Target

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    fetched: int = 0
    new_postings: int = 0
    reposts: int = 0
    per_company: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class FilterResult:
    evaluated: int = 0
    passed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def kill_rate(self) -> float:
        if not self.evaluated:
            return 0.0
        return 1.0 - (self.passed / self.evaluated)


@dataclass
class ScoringGate:
    allowed: bool
    queued: int
    cap: int
    reason: str


def check_scoring_gate(conn, cap: int, enabled: bool) -> ScoringGate:
    """Decide whether Stage B may spend money on this run.

    Two independent brakes, both defaulting to "do not spend":

      * scoring is opt-in. The first run against a fresh database is a
        backfill of every posting every target company currently has open —
        thousands of rows — and it should populate the database, run Stage A,
        and score nothing, so the survivors can be eyeballed before a single
        token is billed.
      * even once enabled, a queue larger than `cap` stops the run instead of
        working through it. A sudden jump in the queue means a filter was
        loosened or a large board was added, and the right response is to look
        rather than to pay.
    """
    queued = len(get_unscored(conn))

    if not enabled:
        return ScoringGate(
            False, queued, cap,
            f"scoring disabled (insert-only). {queued} posting(s) queued. "
            f"Review with `cli audit --passed`, then enable scoring.")

    if queued > cap:
        return ScoringGate(
            False, queued, cap,
            f"{queued} postings queued, above the {cap} cap. This is normal "
            f"on a cold start. Review with `cli audit --passed`, tighten "
            f"filters.yaml, or raise JOBPIPE_MAX_SCORES_PER_RUN deliberately.")

    return ScoringGate(True, queued, cap, f"{queued} posting(s) to score")


def fetch_all(conn, targets: list[Target]) -> FetchResult:
    """Fetch every target board and persist new postings."""
    result = FetchResult()

    for target in targets:
        fetcher = FETCHERS[target.ats_type]
        count = 0
        try:
            for posting in fetcher(target.company, target.board_token):
                count += 1
                posting_id, is_new = db.upsert_posting(
                    conn, posting, posting.content_hash)
                if is_new:
                    result.new_postings += 1
                    if dedupe.resolve_repost(conn, posting_id,
                                             posting.content_hash,
                                             posting.location):
                        result.reposts += 1
            conn.commit()

        except FetchError as exc:
            conn.commit()
            message = f"{target.company} ({target.ats_type}): {exc}"
            result.errors.append(message)
            log.error("  FAILED %s", message)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad board is not fatal
            conn.commit()
            message = (f"{target.company} ({target.ats_type}): "
                       f"unexpected {type(exc).__name__}: {exc}")
            result.errors.append(message)
            log.exception("  FAILED %s", message)
            continue

        result.fetched += count
        result.per_company[target.company] = count

        if count == 0:
            # Not an error — a real board can legitimately have zero open
            # roles — but it is the signature of a stale token, so it is
            # logged at warning level rather than swallowed.
            log.warning("  %-22s %s: 0 postings (check board_token?)",
                        target.company, target.ats_type)
        else:
            log.info("  %-22s %s: %d postings", target.company,
                     target.ats_type, count)

    return result


def filter_all(conn, rules: filters.FilterRules) -> FilterResult:
    """Run Stage A over every posting that has not been filtered yet."""
    result = FilterResult()

    for row in db.get_unfiltered(conn):
        passed, reason = filters.evaluate(row, rules)
        db.set_stage_a(conn, row["id"], passed, reason)
        result.evaluated += 1
        if passed:
            result.passed += 1
        else:
            key = reason.split(":")[0]
            result.reasons[key] = result.reasons.get(key, 0) + 1

    conn.commit()
    return result
