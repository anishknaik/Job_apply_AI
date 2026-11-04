"""auto_submit or review, with reasons. Decided by this rule, never by a model.

PHASE6_PLAN §3's eight conditions, each a named check with a detail string,
so the report can say which one failed. Auto-submit only when every one
holds; otherwise review. Pure: no browser, no database, no clock of its own
- the runner hands in what it read, and the tests hand in what they like.

The conditions, in the plan's order:

1. enabled   - config says auto_submit and this run asked for --auto
2. no_bot_check - no signal before fill, none just before submit
3. no_blockers - every required field filled from a source
4. verified  - every typed value read back equal; every upload this posting's
5. no_required_never_fill - no required attestation, salary, id or payment
6. answers_session_written - every open-ended answer a session-written,
   grounding-passed row (a drafting model's answer is review-only)
7. first_attempt - no prior submit click on this posting, not already applied
8. under_limits - daily cap and spacing

The daily cap is a rolling 24 hours, not a calendar day: stricter, and it
does not reset at midnight for a run that started at 23:55.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import resolve as _resolve
from .config import ApplyConfig

AUTO_SUBMIT, REVIEW = "auto_submit", "review"
DRY_RUN, REVIEW_MODE, AUTO_MODE = "dry_run", "review", "auto"
SESSION_WRITERS = ("claude-mcp",)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Decision:
    outcome: str
    checks: tuple[Check, ...]

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]

    @property
    def auto_submit(self) -> bool:
        return self.outcome == AUTO_SUBMIT


@dataclass(frozen=True)
class Inputs:
    config: ApplyConfig
    mode: str
    resolution: _resolve.Resolution
    unverified: tuple[str, ...] = ()           # field keys that did not read back equal
    posting_dir: Path | None = None            # where this posting's files live
    signals_before: tuple[str, ...] = ()
    signals_at_submit: tuple[str, ...] | None = None   # None: not yet checked
    answer_writers: dict[str, str] = field(default_factory=dict)  # source -> model
    prior_submit_attempts: int = 0
    current_status: str | None = None
    recent_auto_submits: tuple[str, ...] = ()  # ISO timestamps, any order
    now: datetime | None = None
    fill_error: str | None = None


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decide(inp: Inputs) -> Decision:
    checks: list[Check] = []
    res = inp.resolution

    # 1
    if inp.mode != AUTO_MODE:
        checks.append(Check("enabled", False, f"this run is {inp.mode}, not --auto"))
    elif not inp.config.auto_submit:
        checks.append(Check("enabled", False,
                            f"auto_submit is off in {inp.config.source}"))
    else:
        checks.append(Check("enabled", True, "auto_submit on in config and asked for"))

    # 2
    kinds = tuple(inp.signals_before) + tuple(inp.signals_at_submit or ())
    if kinds:
        checks.append(Check("no_bot_check", False,
                            "bot check on the page: " + ", ".join(sorted(set(kinds)))))
    elif inp.signals_at_submit is None:
        checks.append(Check("no_bot_check", False, "not re-checked just before submit"))
    else:
        checks.append(Check("no_bot_check", True, "none seen before fill or before submit"))

    # 3
    blockers = res.blockers
    if inp.fill_error:
        checks.append(Check("no_blockers", False, f"fill did not complete: {inp.fill_error}"))
    elif blockers:
        checks.append(Check("no_blockers", False, "; ".join(
            f"{b.field.label[:60]!r}: {b.note}" for b in blockers)))
    else:
        checks.append(Check("no_blockers", True, "every required field has a source"))

    # 4
    problems = [f"{k} did not read back equal" for k in inp.unverified]
    for item in res.items:
        if item.action == _resolve.UPLOAD and item.value:
            if inp.posting_dir is None:
                problems.append(f"{item.field.key}: no posting folder to check the upload against")
            else:
                try:
                    Path(item.value).resolve().relative_to(Path(inp.posting_dir).resolve())
                except ValueError:
                    problems.append(f"{item.field.key}: {item.value} is not in this posting's folder")
    checks.append(Check("verified", not problems,
                        "; ".join(problems) if problems else "every value read back equal and every upload is this posting's"))

    # 5
    never = [r for r in res.items
             if r.field.required and r.meaning in _resolve.NEVER_FILL]
    checks.append(Check("no_required_never_fill", not never,
                        "; ".join(f"{r.meaning}: {r.field.label[:60]!r}" for r in never)
                        if never else "no required attestation, salary, government id or payment field"))

    # 6
    bad = []
    for item in res.items:
        if item.action == _resolve.FILL and (item.source or "").startswith("documents:"):
            writer = inp.answer_writers.get(item.source)
            if writer not in SESSION_WRITERS:
                bad.append(f"{item.field.label[:50]!r} written by {writer or 'an unrecorded model'}")
    checks.append(Check("answers_session_written", not bad,
                        "; ".join(bad) if bad else "every open-ended answer is a session-written, grounding-passed row"))

    # 7
    if inp.prior_submit_attempts:
        checks.append(Check("first_attempt", False,
                            f"{inp.prior_submit_attempts} prior submit click(s) on this posting"))
    elif inp.current_status == "applied":
        checks.append(Check("first_attempt", False, "already applied"))
    else:
        checks.append(Check("first_attempt", True, "no prior submit click, not applied"))

    # 8
    now = inp.now or datetime.now(timezone.utc)
    stamps = [t for t in (_parse(s) for s in inp.recent_auto_submits) if t]
    window = [t for t in stamps if now - t <= timedelta(hours=24)]
    cap = inp.config.max_auto_submits_per_day
    gap = timedelta(minutes=inp.config.min_minutes_between)
    if len(window) >= cap:
        checks.append(Check("under_limits", False,
                            f"{len(window)} auto-submits in the last 24h, cap {cap}"))
    elif stamps and now - max(stamps) < gap:
        checks.append(Check("under_limits", False,
                            f"last auto-submit {int((now - max(stamps)).total_seconds() // 60)} min ago, "
                            f"minimum {inp.config.min_minutes_between}"))
    else:
        checks.append(Check("under_limits", True,
                            f"{len(window)} of {cap} in the last 24h, spacing ok"))

    outcome = AUTO_SUBMIT if all(c.passed for c in checks) else REVIEW
    return Decision(outcome, tuple(checks))
