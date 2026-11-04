"""Simulate a broken Stage A filter, for the Revenium demo's budget alert.

Step 7 of `revenium-demo-context.md`. Stage A discards ~96% of postings at zero
cost; this scores a sample of what it discarded, as if the filter had stopped
working, so the dashboard shows a spend spike and the `jobpipe_budget_spend`
budget fires.

What it deliberately does NOT do:

  * write scores. The postings failed Stage A for real reasons, and a score row
    would put them in the queue and the sheet. Every result is discarded; only
    the spend and the metering are real.
  * touch filters.yaml. The filter is bypassed by choosing rows, not broken.
  * exceed 300 postings. HARD_CAP is not a flag.

Every call carries transaction_name=broken_filter_spike, so the spike can be
told apart from the normal batch in Revenium.

    python scripts/revenium_spike.py            # sample and estimate, no spend
    python scripts/revenium_spike.py --yes      # run it
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from jobpipe import db, metering, scoring  # noqa: E402
from jobpipe.providers import (default_escalation_model, default_model,  # noqa: E402
                               get_provider)

HARD_CAP = 300

# Per posting, measured on the 176-posting batch of 2026-10-21: GCP billed
# ~$4.01, Revenium metered $4.77 (it prices Gemini cached tokens twice). Both
# shown, because the budget alert fires on Revenium's figure, not Google's.
# An OVERESTIMATE here: that batch escalated 49% to Pro, rejected postings
# only 6% (they score low, outside the 5-7 band). The first 300-posting spike
# on 2026-10-22 cost ~$1.49 billed / $2.35 metered, not $6.84 / $8.13.
PER_POSTING_GCP = 4.01 / 176
PER_POSTING_REVENIUM = 4.77 / 176
SECONDS_PER_POSTING = 49 * 60 / 176


def rejected_sample(conn, n: int):
    """Postings Stage A rejected on their merits, still live, chosen at random
    so the spike spreads across companies the way a broken filter would."""
    return conn.execute("""
        SELECT p.* FROM postings p
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE p.stage_a_passed = 0
          AND (p.stage_a_reason IS NULL
               OR p.stage_a_reason NOT LIKE 'duplicate:%')
          AND s.posting_id IS NULL
          AND p.description IS NOT NULL AND p.description != ''
          AND p.last_seen_at >= ?
        ORDER BY RANDOM() LIMIT ?
    """, (db.utc_days_ago(7), n)).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=HARD_CAP)
    parser.add_argument("--yes", action="store_true",
                        help="required - confirms you intend to spend money")
    args = parser.parse_args()
    if not 1 <= args.n <= HARD_CAP:
        print(f"-n must be 1..{HARD_CAP}.")
        return 1

    if not metering.enabled():
        print("metering is off - REVENIUM_METERING_API_KEY is not set, or "
              "the SDK is missing. Refusing: an unmetered spike shows nothing.")
        return 1

    conn = db.connect("jobpipe.db")
    rows = rejected_sample(conn, args.n)
    companies = len({row["company"] for row in rows})
    n = len(rows)
    print(f"broken-filter spike: {n} Stage-A-rejected posting(s) across "
          f"{companies} companies, on {default_model()} escalating to "
          f"{default_escalation_model()}. Scores are discarded.\n")
    print(f"  estimated cost   ~${n * PER_POSTING_GCP:.2f} billed by GCP")
    print(f"                   ~${n * PER_POSTING_REVENIUM:.2f} as Revenium "
          f"meters it (what the budget sees)")
    print(f"  estimated time   ~{n * SECONDS_PER_POSTING / 60:.0f} min\n")
    for row in rows[:5]:
        print(f"  {row['id']:>6}  {row['company']} - {row['title']}"
              f"  [{row['stage_a_reason']}]")
    if n > 5:
        print(f"  ... and {n - 5} more")
    if not args.yes:
        print("\nAdd --yes to confirm the spend. Nothing has been scored.")
        return 1

    provider = get_provider()
    system_prompt = scoring.build_system_prompt(scoring.load_profile())
    run = scoring.ScoreRun()
    started = time.monotonic()
    with metering.tagged(transaction_name="broken_filter_spike"):
        for index, row in enumerate(rows, start=1):
            scoring.score_posting(provider, row, system_prompt, run)
            if index % 25 == 0:
                print(f"  {index}/{n}  escalated {run.escalated}  "
                      f"est. ~${index * PER_POSTING_REVENIUM:.2f}  "
                      f"{(time.monotonic() - started) / 60:.0f} min",
                      flush=True)

    import revenium_middleware as rm
    flushed = rm.flush_metering(timeout_seconds=60)

    print(f"\nscored {run.scored} (discarded), escalated {run.escalated}, "
          f"failed {run.failed}")
    print(f"input {run.input_tokens}  output {run.output_tokens}  "
          f"cache read {run.cache_read_tokens}  (jobpipe's own count - "
          f"excludes thinking tokens)")
    print(f"flushed {flushed}")
    print(f"metering status: {rm.get_metering_status()}")
    for message in run.errors[:10]:
        print(f"  ! {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
