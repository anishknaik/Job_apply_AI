"""The review wait: the window stays open until Anish is done, and when the
form disappears after his submit the runner keeps the evidence.

Replaces `input()` in the CLI, which ended the run the moment a
non-interactive shell closed stdin and so closed the browser at once. The
loop polls the page every couple of seconds and ends when a line arrives
on stdin (Anish pressing Enter, or the workbench's /fill/close), or when
the browser or page is closed. EOF on stdin is not an ending: the reader
thread just stops reading.

While the form is on the page the latest snapshot of its choice widgets is
kept. When the form goes - his submit, or a navigation - the run folder
gets the confirmation evidence (screenshot, URL, a body-text excerpt, the
page HTML and submit.classify's verdict), the choices he made on fields
the runner did not fill are recorded as learned answers, and both are
printed. Nothing here clicks.

**After his submit the posting is marked applied** (Anish, 2026-11-05: "once
I review and submit, I want the agent to just mark the posting as
applied"). The form leaving the page after he clicked submit is his
confirmation; `ctx.record_applied` is called unless the page shows the
ATS's error box, in which case nothing is recorded and the evidence says
why. Greenhouse's confirmation wording is UNVERIFIED, so a
Greenhouse submit usually reads `unconfirmed` - the form is gone and
nothing was recognised - and that still counts; the saved screenshot and
text are there to check against.

Playwright's sync API must stay on the thread that opened the browser, so
the only thread here reads stdin.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import fill as _fill, learned as _learned, submit as _submit

STDIN, BROWSER_CLOSED, MAX_WAIT = "stdin", "browser_closed", "max_wait"


class StdinLines:
    """Lines from a text stream, read on a daemon thread. `get()` is
    non-blocking and returns None while nothing has arrived. EOF ends the
    reading, never the wait."""

    def __init__(self, stream=None):
        self._queue: queue.Queue = queue.Queue()
        self.eof = False
        self._stream = stream if stream is not None else sys.stdin
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                line = self._stream.readline()
                if not line:
                    break
                self._queue.put(line)
        except Exception:                                    # noqa: BLE001
            pass
        self.eof = True

    def get(self) -> str | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


@dataclass
class ReviewContext:
    """What the wait needs from the run that opened the window."""
    page: object
    ats: str
    form: object
    resolution: object
    company: str
    posting_id: int
    run_dir: Path
    learned_store: object = None
    learned_path: Path | None = None
    record_applied: object = None    # () -> recorded status; None records nothing


# Verdicts after the form leaves that mean it did NOT go through. Only the
# ATS's own error box: classify() also says `challenge` whenever reCAPTCHA's
# script is still on the page, which it is after a successful submit on
# every form seen so far; a challenge Anish actually faced sits over the
# form, and the form is then still there.
NOT_SUBMITTED = (_submit.ERROR,)


@dataclass
class ReviewResult:
    ended_by: str
    form_gone: bool = False
    evidence: dict | None = None
    choices: dict = field(default_factory=dict)
    learned_recorded: list[str] = field(default_factory=list)
    status_recorded: str | None = None
    waited: float = 0.0


def _page_alive(page) -> bool:
    try:
        return not page.is_closed()
    except Exception:                                        # noqa: BLE001
        return False


def capture_evidence(page, ats: str, run_dir: Path) -> dict:
    """What the page shows once the form is gone, kept for the record and
    for verifying the confirmation patterns against a real page."""
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"url": None, "verdict": None, "text_excerpt": "", "screenshot": None,
                      "html": None, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        html, url, text, verdict = _submit._read(page)
    except Exception as exc:                                 # noqa: BLE001
        evidence["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return evidence
    evidence["url"] = url
    evidence["verdict"] = _submit.classify(ats, html, url, text, verdict)
    evidence["signals"] = list(verdict.kinds)
    evidence["text_excerpt"] = " ".join(text.split())[:2000]
    try:
        shot = run_dir / "after-submit.png"
        page.screenshot(path=str(shot), full_page=True)
        evidence["screenshot"] = str(shot)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        page_file = run_dir / "after-submit.html"
        page_file.write_text(html, encoding="utf-8")
        evidence["html"] = str(page_file)
    except Exception:                                        # noqa: BLE001
        pass
    (run_dir / "confirmation.json").write_text(json.dumps(evidence, indent=1), encoding="utf-8")
    return evidence


def wait(ctx: ReviewContext, lines: StdinLines | None = None, *, poll: float = 2.0,
         max_wait: float | None = None, out=None) -> ReviewResult:
    """Keep the window open and watch the form. See the module docstring."""
    lines = lines or StdinLines()
    out = out or sys.stdout
    started = time.monotonic()
    result = ReviewResult(ended_by=STDIN)
    print("The browser stays open for review. Submit the form yourself, then press "
          "Enter here to close it (closing the window works too).", file=out, flush=True)
    while True:
        if lines.get() is not None:
            result.ended_by = STDIN
            break
        if not _page_alive(ctx.page):
            result.ended_by = BROWSER_CLOSED
            break
        if max_wait is not None and time.monotonic() - started >= max_wait:
            result.ended_by = MAX_WAIT
            break
        try:
            frame, _root = _fill.form_root(ctx.page, ctx.ats)
            if frame is not None:
                result.choices = _fill.read_choices(ctx.page, ctx.form) or result.choices
            elif not result.form_gone:
                result.form_gone = True
                result.evidence = capture_evidence(ctx.page, ctx.ats, ctx.run_dir)
                result.learned_recorded = _record(ctx, result.choices)
                verdict = (result.evidence or {}).get("verdict")
                if ctx.record_applied is not None and verdict not in NOT_SUBMITTED:
                    try:
                        result.status_recorded = ctx.record_applied()
                    except Exception as exc:                 # noqa: BLE001
                        print(f"  could not record applied: {exc}", file=out, flush=True)
                _print_evidence(result, out)
        except Exception as exc:                             # noqa: BLE001
            if not _page_alive(ctx.page):
                result.ended_by = BROWSER_CLOSED
                break
            print(f"  (page not readable this poll: {type(exc).__name__})", file=out, flush=True)
        _sleep(ctx.page, poll)
    result.waited = time.monotonic() - started
    return result


def _sleep(page, seconds: float) -> None:
    try:
        page.wait_for_timeout(seconds * 1000)
    except Exception:                                        # noqa: BLE001
        time.sleep(seconds)


def _record(ctx: ReviewContext, choices: dict) -> list[str]:
    if ctx.learned_store is None or not choices:
        return []
    recorded = _learned.record_choices(ctx.learned_store, ctx.form, ctx.resolution, choices,
                                       ctx.company, ctx.posting_id)
    if recorded:
        _learned.save(ctx.learned_store, ctx.learned_path)
    return recorded


def _print_evidence(result: ReviewResult, out) -> None:
    ev = result.evidence or {}
    print("\nThe form is gone. What the page shows now:", file=out)
    print(f"  url: {ev.get('url')}", file=out)
    print(f"  verdict: {ev.get('verdict') or 'no confirmation recognised'}"
          + (" (Greenhouse patterns are UNVERIFIED - compare with the screenshot)"
             if ev.get("verdict") != "confirmed" else ""), file=out)
    print(f"  text: {ev.get('text_excerpt', '')[:300]}", file=out)
    print(f"  saved: {ev.get('screenshot')}, {ev.get('html')}", file=out)
    if result.learned_recorded:
        print(f"  remembered {len(result.learned_recorded)} answer(s): "
              f"{', '.join(result.learned_recorded)}", file=out)
    else:
        print("  remembered no answers (nothing chosen on a learnable question the runner left)", file=out)
    if result.status_recorded:
        print(f"  status recorded: {result.status_recorded} (run `python -m jobpipe.cli sync` "
              "to put it in the sheet)", file=out, flush=True)
    elif ev.get("verdict") in NOT_SUBMITTED:
        print(f"  status not changed: the page shows {ev.get('verdict')}, so it did not go through.",
              file=out, flush=True)
    else:
        print("  status not changed: record `applied` after checking the confirmation.",
              file=out, flush=True)
