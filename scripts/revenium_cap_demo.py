"""Cap Pro with Revenium's circuit breaker, for the demo's control beat.

The budget alert (`revenium_spike.py`) shows Revenium *noticing* spend. This
shows it *stopping* spend: a cap rule with action BLOCK, scoped to task type
`score_pro`, refuses the Pro escalation before the request leaves the process,
and scoring carries on with the Flash score - `score_posting` treats a failed
escalation as non-fatal.

It re-scores postings whose stored score came from gemini-2.5-pro. They
escalated before, so Flash will likely land in the 5-7 band again and try Pro.

What it deliberately does NOT do:

  * write scores. A posting whose Pro call was blocked would be stored on
    Flash alone, without the second opinion its last score had. Every result
    is discarded; only the spend, the metering and the blocks are real.
  * exceed 10 postings. HARD_CAP is not a flag.
  * run with the breaker off. Without it this is just a small scoring run.

The breaker is opt-in and read from the environment. Set it for this run only:

    REVENIUM_CIRCUIT_BREAKER_ENABLED=true REVENIUM_TEAM_ID=<id> \\
        python scripts/revenium_cap_demo.py            # rules + sample, no spend
    ... python scripts/revenium_cap_demo.py --yes      # run it

Without --yes it still reads the team's rules from Revenium, which costs
nothing and is the check that the team id is right.

Timing matters. The SDK never decides a breach itself: Revenium does, from the
metering it has ingested, and the SDK's poller picks the verdict up. So there
is a lag between the call that crosses the cap and the first call it blocks.
`--pause` spaces the postings out so the lag shows as "allowed, then blocked"
rather than every call getting through before the verdict lands.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Before anything starts the poller: the SDK default is 60 s, which would
# leave most of a 10-posting run judged against a stale verdict.
os.environ.setdefault("REVENIUM_CB_POLL_INTERVAL_SECONDS", "15")
# The SDK's default is the origin of the metering URL, api.revenium.ai, where
# the rules feed answers 404 for a real team. It lives under /profitstream
# (found 2026-10-22; the README shows it only as an "optional" example).
# Without this the breaker silently fails open and nothing is ever blocked.
os.environ.setdefault("REVENIUM_ENFORCEMENT_BASE_URL",
                      "https://api.revenium.ai/profitstream")

from jobpipe import db, metering, scoring  # noqa: E402
from jobpipe.providers import (default_escalation_model, default_model,  # noqa: E402
                               get_provider)

HARD_CAP = 10
TRANSACTION = "pro_cap_demo"

# One Flash call per posting plus one Pro call each for the ones the cap lets
# through, at the 2026-10-21 batch's metered rates: $0.90 / 178 Flash runs and
# $3.87 / 88 Pro runs. An upper bound, as if every Pro call got through.
PER_FLASH = 0.90 / 178
PER_PRO = 3.87 / 88


@dataclass
class Call:
    model: str
    outcome: str                # "ok", "blocked" or "error"
    detail: str = ""


@dataclass
class Recorder:
    """Wraps the provider and remembers what happened to each call.

    `score_posting` folds a blocked escalation into the same path as any other
    failure, so the outcome is read here, at the provider, where the cause is
    still attached.
    """
    inner: object
    calls: list[Call] = field(default_factory=list)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def score(self, **kwargs):
        model = kwargs["model"]
        try:
            response = self.inner.score(**kwargs)
        except Exception as exc:
            blocked = _budget_block(exc)
            if blocked is not None:
                self.calls.append(Call(model, "blocked", _describe(blocked)))
            else:
                self.calls.append(Call(model, "error", str(exc)[:120]))
            raise
        self.calls.append(Call(model, "ok"))
        return response


def _budget_block(exc):
    from revenium_middleware import BudgetExceededError
    while exc is not None:
        if isinstance(exc, BudgetExceededError):
            return exc
        exc = exc.__cause__
    return None


def _describe(block) -> str:
    parts = [block.rule_name or "rule"]
    if block.current_value is not None and block.threshold is not None:
        parts.append(f"${block.current_value:.4f} of ${block.threshold:.2f}")
    return ", ".join(parts)


def pro_scored_sample(conn, n: int):
    """Postings whose stored score is Pro's, newest first: the 2.5-era batch."""
    return conn.execute("""
        SELECT p.*, s.overall AS stored_overall FROM postings p
        JOIN scores s ON s.posting_id = p.id
        WHERE s.model = 'gemini-2.5-pro'
          AND p.description IS NOT NULL AND p.description != ''
        ORDER BY s.scored_at DESC LIMIT ?
    """, (n,)).fetchall()


def show_rules(label: str) -> None:
    """What Revenium reports for this team's rules, read fresh, not cached.

    `_fetch_rules` is private to the SDK. It is used here because it is the
    exact read the breaker's poller makes, so what it prints is what the
    breaker sees - which is the question when a cap does not seem to fire.
    """
    from revenium_middleware._core import enforcement
    fetched = enforcement._fetch_rules()
    print(f"{label}:")
    if fetched is None:
        print("  could not read the rules - wrong REVENIUM_TEAM_ID, or the "
              "key cannot read enforcement rules.")
        return
    if not fetched.rules:
        print("  no rules for this team. Create the cap in Guardrails first.")
        return
    for rule in fetched.rules:
        filters = ", ".join(
            f"{f.get('dimension', '?')} {f.get('operator', '?')} "
            f"{f.get('value', f.get('values', '?'))}"
            for f in (rule.get("filters") or []) if isinstance(f, dict))
        print(f"  {rule.get('name', '?')}  action={rule.get('action', '?')}  "
              f"breached={rule.get('breached', rule.get('blocked'))}  "
              f"current={rule.get('currentValue')}  "
              f"threshold={rule.get('threshold')}  "
              f"[{filters or 'no filters'}]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=8)
    parser.add_argument("--pause", type=float, default=20.0,
                        help="seconds between postings, so a breach can "
                             "reach the breaker (default 20)")
    parser.add_argument("--yes", action="store_true",
                        help="required - confirms you intend to spend money")
    args = parser.parse_args()
    if not 1 <= args.n <= HARD_CAP:
        print(f"-n must be 1..{HARD_CAP}.")
        return 1

    if not metering.enabled():
        print("metering is off - REVENIUM_METERING_API_KEY is not set, or "
              "the SDK is missing. Refusing: the cap is judged on metering.")
        return 1
    from revenium_middleware._core.enforcement import is_circuit_breaker_enabled
    if not is_circuit_breaker_enabled():
        print("the circuit breaker is off. Set "
              "REVENIUM_CIRCUIT_BREAKER_ENABLED=true for this run.")
        return 1
    if not (os.environ.get("REVENIUM_TEAM_ID") or "").strip():
        print("REVENIUM_TEAM_ID is not set; the breaker cannot fetch rules "
              "without it.")
        return 1

    conn = db.connect("jobpipe.db")
    rows = pro_scored_sample(conn, args.n)
    n = len(rows)
    print(f"pro cap demo: {n} posting(s) last scored by gemini-2.5-pro, on "
          f"{default_model()} escalating to {default_escalation_model()}. "
          f"Scores are discarded.\n")
    print(f"  estimated cost   at most ~${n * (PER_FLASH + PER_PRO):.2f} "
          f"metered, if the cap blocks nothing")
    print(f"  estimated time   ~{n * (args.pause + 25) / 60:.0f} min "
          f"({args.pause:.0f} s pause between postings)\n")
    for row in rows:
        print(f"  {row['id']:>6}  stored {row['stored_overall']}  "
              f"{row['company']} - {row['title']}")
    print()
    show_rules("rules before")
    if not args.yes:
        print("\nAdd --yes to confirm the spend. Nothing has been scored.")
        return 1

    provider = Recorder(get_provider())
    system_prompt = scoring.build_system_prompt(scoring.load_profile())
    run = scoring.ScoreRun()
    blocked_total = passed_total = 0
    print()
    with metering.tagged(transaction_name=TRANSACTION):
        for index, row in enumerate(rows, start=1):
            if index > 1:
                time.sleep(args.pause)
            provider.calls.clear()
            score = scoring.score_posting(provider, row, system_prompt, run)
            flash = next((c for c in provider.calls
                          if c.model == default_model()), None)
            pro = [c for c in provider.calls
                   if c.model == default_escalation_model()]
            if score is None:
                line = f"Flash failed ({flash.detail if flash else '?'})"
            else:
                first = (score["overall"] if score["model"] == default_model()
                         else "?")
                if not pro:
                    line = f"Flash {first}, not in the band, no Pro call"
                elif any(c.outcome == "ok" for c in pro):
                    passed_total += 1
                    line = f"Pro PASSED -> {score['overall']}"
                elif any(c.outcome == "blocked" for c in pro):
                    blocked_total += 1
                    line = (f"Flash {first}, Pro BLOCKED ({pro[0].detail}); "
                            f"kept the Flash score")
                else:
                    line = f"Flash {first}, Pro errored ({pro[0].detail})"
            print(f"  {index}/{n}  {row['id']:>6}  {row['company'][:24]:<24} "
                  f"{line}", flush=True)

    import revenium_middleware as rm
    flushed = rm.flush_metering(timeout_seconds=60)

    print(f"\nscored {run.scored} (discarded), Pro passed {passed_total}, "
          f"Pro blocked {blocked_total}, failed {run.failed}")
    print(f"flushed {flushed}")
    print(f"metering status: {rm.get_metering_status()}\n")
    show_rules("rules after")
    for message in run.errors[:10]:
        print(f"  ! {message}")
    print("\nTurn the cap off once the screenshots are taken, or the next "
          "real scoring run is silently Flash-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
