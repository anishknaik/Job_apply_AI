"""Score a handful of backlog postings with Revenium metering on, then report
what reached Revenium. Step 5 of `revenium-demo-context.md`.

`cli score` cannot do this: its cap refuses a queue larger than the cap rather
than truncating it, so with a 208-posting backlog there is no small run. This
drives `scoring.score_pending` - the same loop, provenance and commits - with
its own small limit. The scores are real and are kept: they are backlog work,
not demo-only spend.

    python scripts/revenium_smoke.py            # preview, no spend
    python scripts/revenium_smoke.py --yes      # score 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from jobpipe import db, metering, scoring  # noqa: E402
from jobpipe.providers import (default_escalation_model, default_model,  # noqa: E402
                               get_provider)

HARD_CAP = 10


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5)
    parser.add_argument("--yes", action="store_true",
                        help="required - confirms you intend to spend money")
    args = parser.parse_args()
    if not 1 <= args.n <= HARD_CAP:
        print(f"-n must be 1..{HARD_CAP}; this is a smoke test, not a run.")
        return 1

    if not metering.enabled():
        print("metering is off - REVENIUM_METERING_API_KEY is not set, or "
              "the SDK is missing. Refusing: an unmetered smoke test proves "
              "nothing.")
        return 1

    conn = db.connect("jobpipe.db")
    rows = db.get_unscored(conn, limit=args.n)
    print(f"would score {len(rows)} posting(s) on {default_model()} "
          f"(escalating to {default_escalation_model()}):")
    for row in rows:
        print(f"  {row['id']:>6}  {row['company']} - {row['title']}")
    if not args.yes:
        print("\nAdd --yes to confirm the spend. Nothing has been scored.")
        return 1

    provider = get_provider()
    system_prompt = scoring.build_system_prompt(scoring.load_profile())
    run = scoring.score_pending(conn, provider, system_prompt, limit=args.n,
                                rows=rows)

    import revenium_middleware as rm
    flushed = rm.flush_metering(timeout_seconds=30)

    print(f"\nscored {run.scored}, escalated {run.escalated}, "
          f"failed {run.failed}")
    print(f"input {run.input_tokens}  output {run.output_tokens}  "
          f"cache read {run.cache_read_tokens}  (jobpipe's own count - "
          f"excludes thinking tokens)")
    print(f"flushed {flushed}")
    print(f"metering status: {rm.get_metering_status()}")
    print(f"buffer: {rm.get_buffer_stats()}")
    for message in run.errors:
        print(f"  ! {message}")
    return 0 if run.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
