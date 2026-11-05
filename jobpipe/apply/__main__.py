"""python -m jobpipe.apply <posting_id> [--dry-run | --review | --auto]

Dry run is the default and touches nothing. --review fills the form and
leaves the browser open for Anish to check and submit. --auto also fills,
and clicks submit once only if decide.py's eight conditions all hold - and
only if config/apply.yaml, gitignored, says auto_submit: true; without
that the flag is refused here, before a browser opens.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobpipe.apply",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("posting_id", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="resolve and report; fill nothing (default)")
    mode.add_argument("--review", action="store_true", help="fill, stop, leave the browser open")
    mode.add_argument("--auto", action="store_true",
                      help="fill and submit once if every condition holds; needs auto_submit: true in config/apply.yaml")
    parser.add_argument("--db", default=None)
    parser.add_argument("--config", default=None, help="apply.yaml path (default config/apply.yaml)")
    parser.add_argument("--headless", action="store_true", help="no window (dry runs; never for review)")
    parser.add_argument("--no-wait", action="store_true",
                        help="close the browser when the run ends instead of waiting for Enter")
    parser.add_argument("--url", default=None, help="open this page instead of the ATS (a saved page)")
    args = parser.parse_args(argv)

    os.chdir(REPO_ROOT)
    from ..cli import _load_env
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from .. import mcp_server as tools
    from ..targets import load_targets
    from . import config as _config, decide, runner

    cfg = _config.load(args.config)
    run_mode = decide.AUTO_MODE if args.auto else decide.REVIEW_MODE if args.review else decide.DRY_RUN
    if run_mode == decide.AUTO_MODE and not cfg.auto_submit:
        print("--auto refused: auto_submit is not true in config/apply.yaml "
              "(copy config/apply.example.yaml and set it by hand). Nothing was opened.")
        return 2
    if run_mode == decide.REVIEW_MODE and args.headless:
        print("--review needs a window: Anish reviews and submits in it. Dropping --headless.")
        args.headless = False

    try:
        targets = load_targets()
    except Exception as exc:                                 # noqa: BLE001
        print(f"targets.csv not loaded ({exc}); Greenhouse tokens will come from posting URLs")
        targets = None

    conn = tools._conn(args.db)
    wait = run_mode != decide.DRY_RUN and not args.no_wait and not args.headless
    try:
        outcome = runner.run(conn, args.posting_id, run_mode, cfg, targets=targets,
                             form_url=args.url, headless=args.headless or None,
                             keep_open=wait)
    except runner.RunnerError as exc:
        print(f"not run: {exc}")
        return 1
    finally:
        conn.close()

    print(runner.report_text(outcome))
    if outcome.browser is not None:
        from . import review
        try:
            if outcome.review is not None:
                result = review.wait(outcome.review)
                print(f"review ended: {result.ended_by}"
                      + (", form gone" if result.form_gone else ", form still on the page"))
        finally:
            outcome.browser.close()
    return 0 if outcome.error is None else 1


if __name__ == "__main__":
    sys.exit(main())
