#!/usr/bin/env python3
"""Daily entry point. Cron runs this and nothing else.

Guarantees, in priority order:

  1. A row is always written to `runs`, including on an unhandled exception.
     A failure that leaves no trace is a failure you find out about in three
     weeks; a failure recorded in `runs` is one you see the next morning.
  2. One company's endpoint failing never aborts the run.
  3. Logs go to a rotating file so a 6am failure is still readable at 9am.

Stage B (scoring) and the Sheets sync are wired in here once the master
profile exists; until then this runs discovery and Stage A only.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "jobpipe.log"


def configure_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=7, encoding="utf-8")
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def main() -> int:
    configure_logging()
    log = logging.getLogger("run_daily")

    from jobpipe import db, filters, pipeline
    from jobpipe.targets import load_targets

    conn = db.connect()
    db.init_schema(conn)
    run_id = db.start_run(conn)

    fetched = new_postings = stage_a_pass = scored = 0
    errors: list[str] = []

    try:
        targets = load_targets()
        log.info("run %d starting: %d target boards", run_id, len(targets))

        fetch_result = pipeline.fetch_all(conn, targets)
        fetched = fetch_result.fetched
        new_postings = fetch_result.new_postings
        errors.extend(fetch_result.errors)
        log.info("fetch: %d postings, %d new, %d reposts, %d board errors",
                 fetch_result.fetched, fetch_result.new_postings,
                 fetch_result.reposts, len(fetch_result.errors))

        rules = filters.load_rules()
        filter_result = pipeline.filter_all(conn, rules)
        stage_a_pass = filter_result.passed
        log.info("stage A: %d evaluated, %d passed, %.1f%% killed",
                 filter_result.evaluated, filter_result.passed,
                 filter_result.kill_rate * 100)

        # Stage B. Scoring is opt-in and capped: the first run against a fresh
        # database backfills every open req at every target company, and that
        # is not a bill worth paying before looking at what survived Stage A.
        cap = int(os.environ.get("JOBPIPE_MAX_SCORES_PER_RUN", "200"))
        enabled = os.environ.get("JOBPIPE_ENABLE_SCORING", "").lower() in (
            "1", "true", "yes")
        gate = pipeline.check_scoring_gate(conn, cap=cap, enabled=enabled)
        log.info("stage B: %s", gate.reason)

        if gate.allowed:
            from jobpipe import scoring
            from jobpipe.cli import _build_provider, _resolve_model

            provider = _build_provider(None)
            score_model = _resolve_model(provider, None)
            system_prompt = scoring.build_system_prompt(scoring.load_profile())
            scoring.check_cacheable(provider, system_prompt, score_model)

            score_run = scoring.score_pending(
                conn, provider, system_prompt, limit=cap, model=score_model)
            scored = score_run.scored
            errors.extend(score_run.errors)
            log.info("stage B: %d scored, %d escalated, %d failed "
                     "(in=%d out=%d cached=%d)",
                     score_run.scored, score_run.escalated, score_run.failed,
                     score_run.input_tokens, score_run.output_tokens,
                     score_run.cache_read_tokens)

        # Sheets sync. A failure here must not lose the scoring already paid
        # for, so it is caught separately and reported rather than raised.
        try:
            from jobpipe import sheets
            from jobpipe.targets import tier_thresholds

            rows = db.get_sheet_rows(conn, min_score=4,
                                     tier_thresholds=tier_thresholds(targets),
                                     limit=int(os.environ.get(
                                         "JOBPIPE_SHEET_LIMIT", "20")))
            if rows:
                worksheet = sheets.open_worksheet()
                sync_result = sheets.sync(
                    worksheet, [sheets.build_row(r) for r in rows])
                sheets.sort_by_score(worksheet)
                log.info("sheet: %d row(s) — %d added, %d updated",
                         len(rows), sync_result.appended, sync_result.updated)
            else:
                log.info("sheet: nothing above the floor to surface")
        except Exception as exc:  # noqa: BLE001 - never lose the scoring
            errors.append(f"sheet sync: {exc}")
            log.error("sheet sync failed (scores are safe in SQLite): %s", exc)

    except Exception as exc:  # noqa: BLE001 - the whole point is to record it
        errors.append(f"FATAL {type(exc).__name__}: {exc}")
        log.error("run %d failed: %s", run_id, exc)
        log.debug("%s", traceback.format_exc())
        db.finish_run(conn, run_id, fetched=fetched, new_postings=new_postings,
                      stage_a_pass=stage_a_pass, scored=scored,
                      errors="\n".join(errors))
        return 1

    db.finish_run(conn, run_id, fetched=fetched, new_postings=new_postings,
                  stage_a_pass=stage_a_pass, scored=scored,
                  errors="\n".join(errors))
    log.info("run %d complete", run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
