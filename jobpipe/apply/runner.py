"""One run of the apply runner on one posting: queue row, lock, browser,
spec, sources, resolve, fill, decide, maybe the one click, record.

The runner is the only place the pieces meet, and it is deliberately thin:
every judgment is in resolve.py and decide.py, every browser action in
fill.py and submit.py, every write a tool body or an apply_runs row. It
never writes prose and never renders a resume - those happen in a session
first, and a posting without them resolves to blockers.

Modes: dry_run (open the page, resolve, report, touch nothing - the
default), review (fill and stop; the posting becomes ready_for_review),
auto (fill, decide, and click once only if decide.py says so; otherwise
exactly what review does). A run that clicked records applied only after
the ATS confirmed, and submit_unconfirmed for anything else.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.server.mcpserver.exceptions import ToolError

from .. import answers as _answers, db, letters, mcp_server as tools
from . import decide as _decide, employers as _employers, fill as _fill
from . import resolve as _resolve
from . import spec as _spec, submit as _submit
from .browser import Browser
from .config import ApplyConfig

log = logging.getLogger(__name__)

LOCK_NAME = ".runner.lock"
LOCK_STALE_AFTER = timedelta(hours=2)


class RunnerError(Exception):
    """A run that could not start or could not be recorded honestly."""


class RunnerBusy(RunnerError):
    """Another run holds the lock. Two browsers on two forms at once is how
    a submit lands on the wrong posting."""


@dataclass
class RunOutcome:
    run_id: int
    posting_id: int
    mode: str
    decision: str
    reasons: list[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    submit_outcome: str | None = None
    status_recorded: str | None = None
    screenshots: list[str] = field(default_factory=list)
    error: str | None = None
    form_url: str | None = None
    browser: object = None          # set only with keep_open; the caller closes it


# ---------------------------------------------------------------------------
# Where the form is
# ---------------------------------------------------------------------------

def board_token(row, targets: list | None) -> str | None:
    """A Greenhouse board token for the posting's company, from targets.csv
    or from the posting's own URL."""
    company = (row["company"] or "").strip().lower()
    for t in targets or []:
        if t.ats_type == _spec.GREENHOUSE and t.company.strip().lower() == company:
            return t.board_token
    url = urlparse(row["url"] or "")
    if url.hostname and url.hostname.endswith("greenhouse.io"):
        qs = parse_qs(url.query)
        if qs.get("for"):
            return qs["for"][0]
        parts = [p for p in url.path.split("/") if p]
        if len(parts) >= 3 and parts[1] == "jobs":
            return parts[0]
        if len(parts) >= 1 and parts[0] not in ("embed",):
            return parts[0]
    return None


def form_url_for(row, targets: list | None) -> tuple[str, str]:
    """(ats, url of the form page). Raises RunnerError when it cannot say."""
    ats = (row["ats_type"] or "").strip().lower()
    if ats == _spec.GREENHOUSE:
        token = board_token(row, targets)
        if not token:
            raise RunnerError(
                f"no Greenhouse board token for {row['company']!r}: add it to "
                f"config/targets.csv")
        return ats, _spec.greenhouse_form_url(token, row["source_id"])
    if ats == _spec.ASHBY:
        if not (row["url"] or "").startswith("https://jobs.ashbyhq.com/"):
            raise RunnerError(
                f"Ashby posting {row['id']} has no jobs.ashbyhq.com URL "
                f"({row['url']!r}); the runner opens only Ashby's own page")
        return ats, _spec.ashby_application_url(row["url"])
    raise RunnerError(f"{ats!r} forms are not handled; Phase 6 covers "
                      f"Greenhouse and Ashby (PHASE6_PLAN §8 for the rest)")


# ---------------------------------------------------------------------------
# What the resolver may draw on
# ---------------------------------------------------------------------------

def build_sources(conn, posting_id: int, form: _spec.FormSpec, *,
                  answers_loader=tools.form_answers,
                  prepare_letter=tools.prepare_letter,
                  employers_loader=_employers.load,
                  company: str | None = None) -> tuple[_resolve.Sources, list[str], dict[str, str]]:
    """Sources from the tool bodies. Returns (sources, notes, answer_writers).
    A tool's refusal becomes a note and an absent source, never a guess."""
    notes: list[str] = []
    company = company or form.company or None
    employers = employers_loader()
    if employers is None:
        notes.append("past employers unknown: bullets.yaml not loaded")
    try:
        answers = answers_loader()
    except ToolError as exc:
        answers, note = None, f"form_answers unavailable: {exc}"
        notes.append(note)

    resume_path = None
    resume = db.current_document(conn, posting_id, "resume")
    if resume is not None and resume["generated_path"] and Path(resume["generated_path"]).is_file():
        resume_path = Path(resume["generated_path"])
    elif resume is not None:
        notes.append(f"resume row {resume['id']} points at a missing file; render_resume again")
    else:
        notes.append("no resume rendered for this posting")

    letter_text = letter_pdf = None
    wants_file = any(_resolve.meaning_of(f) == "cover_letter" and f.kind == _spec.FILE for f in form.fields)
    # A letter text box, or an "anything else" textarea the reviewed letter
    # goes into when there is one (resolve.py, additional_info).
    wants_text = any(_resolve.meaning_of(f) in ("cover_letter", "additional_info")
                     and f.kind == _spec.TEXTAREA for f in form.fields)
    if wants_file or wants_text:
        try:
            out = prepare_letter(conn, posting_id, pdf=wants_file)
            letter_text = out.get("text")
            if out.get("pdf_path"):
                letter_pdf = Path(out["pdf_path"])
            if out.get("warning"):
                notes.append(f"prepare_letter: {out['warning']}")
        except ToolError as exc:
            notes.append(f"prepare_letter refused: {exc}")

    short: dict[str, dict] = {}
    writers: dict[str, str] = {}
    for row in db.current_documents(conn, posting_id, _answers.DOC_TYPE):
        entry = dict(row)
        short[_answers.normalise_question(entry["question"] or "")] = entry
        writers[f"documents:short_answer#{entry['id']}"] = entry.get("model") or ""

    return (_resolve.Sources(answers=answers, resume_path=resume_path,
                             letter_text=letter_text, letter_pdf=letter_pdf,
                             short_answers=short, company=company,
                             employers=employers), notes, writers)


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------

class _Lock:
    def __init__(self, directory: Path):
        self.path = Path(directory) / LOCK_NAME

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                self.path.stat().st_mtime, tz=timezone.utc)
            if age < LOCK_STALE_AFTER:
                raise RunnerBusy(f"another run holds {self.path} (pid "
                                 f"{self.path.read_text(errors='ignore').strip() or '?'})")
            self.path.unlink()
        fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return False


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _wait_for_form(page, ats: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame, _ = _fill.form_root(page, ats)
        if frame is not None:
            return True
        page.wait_for_timeout(250)
    return False


def _resolution_report(resolution: _resolve.Resolution) -> list[dict]:
    """A dry run's field-by-field account: what would go where, hashed."""
    out = []
    for item in resolution.items:
        out.append({"key": item.field.key, "label": item.field.label,
                    "action": item.action, "source": item.source,
                    "meaning": item.meaning, "note": item.note,
                    "value_sha256": _fill.sha256(item.value) if item.value else None,
                    "status": "planned" if item.action in (
                        _resolve.FILL, _resolve.UPLOAD, _resolve.SELECT,
                        _resolve.AUTOCOMPLETE) else item.action})
    return out


def run(conn, posting_id: int, mode: str, config: ApplyConfig, *,
        targets: list | None = None, fetch_json=None, form_url: str | None = None,
        browser_factory=Browser, headless: bool | None = None,
        answers_loader=tools.form_answers, prepare_letter=tools.prepare_letter,
        employers_loader=_employers.load,
        now: datetime | None = None, keep_open: bool = False) -> RunOutcome:
    """Run the apply runner once on one posting. See the module docstring
    for the three modes. `form_url` overrides the page (tests, or a saved
    page); `fetch_json` fetches Greenhouse's questions JSON; `keep_open`
    hands the browser back on the outcome instead of closing it, so a
    review-mode window stays on the filled form until the caller closes
    it."""
    if mode not in (_decide.DRY_RUN, _decide.REVIEW_MODE, _decide.AUTO_MODE):
        raise RunnerError(f"unknown mode {mode!r}")
    row = db.get_posting(conn, posting_id)
    if row is None:
        raise RunnerError(f"no posting with id {posting_id}")
    ats, url = form_url_for(row, targets)
    url = form_url or url

    if fetch_json is None:
        from ..fetchers.http import get_json as fetch_json

    run_id = db.start_apply_run(conn, posting_id, mode, ats)
    outcome = RunOutcome(run_id=run_id, posting_id=posting_id, mode=mode,
                         decision="error", form_url=url)
    shots = Path(config.output_dir) / letters.slug(row) / f"run-{run_id}"
    try:
        with _Lock(Path(config.output_dir)):
            _run_in_browser(conn, row, ats, url, mode, config, outcome, shots,
                            targets=targets, fetch_json=fetch_json,
                            browser_factory=browser_factory, headless=headless,
                            answers_loader=answers_loader,
                            prepare_letter=prepare_letter,
                            employers_loader=employers_loader, now=now,
                            keep_open=keep_open)
    except RunnerBusy:
        db.finish_apply_run(conn, run_id, decision="error", error="runner busy")
        raise
    except Exception as exc:                                 # noqa: BLE001
        outcome.error = f"{type(exc).__name__}: {exc}"[:500]
        outcome.decision = "error"
        log.exception("apply run %s failed", run_id)
    db.finish_apply_run(conn, run_id, decision=outcome.decision,
                        reasons=outcome.reasons, report=outcome.report,
                        screenshots=outcome.screenshots,
                        submit_outcome=outcome.submit_outcome,
                        error=outcome.error)
    return outcome


def _run_in_browser(conn, row, ats, url, mode, config, outcome, shots, *,
                    targets, fetch_json, browser_factory, headless,
                    answers_loader, prepare_letter, employers_loader, now,
                    keep_open):
    posting_id = row["id"]
    form = None
    if ats == _spec.GREENHOUSE:
        token = board_token(row, targets)
        payload = fetch_json(_spec.greenhouse_questions_url(token, row["source_id"]))
        form = _spec.parse_greenhouse(payload, company=row["company"])

    browser = browser_factory(config, headless=headless).__enter__()
    try:
        _drive(conn, row, posting_id, ats, url, mode, config, outcome, shots,
               browser, answers_loader, prepare_letter, employers_loader, now, form)
    finally:
        if keep_open and outcome.error is None and mode != _decide.DRY_RUN:
            outcome.browser = browser
        else:
            browser.close()


def _drive(conn, row, posting_id, ats, url, mode, config, outcome, shots,
           browser, answers_loader, prepare_letter, employers_loader, now, form):
    if True:
        page = browser.new_page()
        page.goto(url, wait_until="load")
        if not _wait_for_form(page, ats, config.browser.page_timeout):
            outcome.error = f"no {_fill.FORM_ROOTS[ats]} on {url} within {config.browser.page_timeout:.0f}s"
            outcome.report = {"signals_before": list(_fill.collect_signals(page).kinds)}
            _screenshot(page, shots, "no-form", outcome)
            return
        if form is None:
            html = page.content()
            form = _spec.parse_ashby_form(html, _spec.extract_app_data(html), apply_url=url)

        sources, notes, writers = build_sources(
            conn, posting_id, form, answers_loader=answers_loader,
            prepare_letter=prepare_letter, employers_loader=employers_loader,
            company=row["company"])
        resolution = _resolve.resolve(form, sources)
        signals_before = tuple(_fill.collect_signals(page).kinds)
        letter_required = any(_resolve.meaning_of(f) == "cover_letter" and f.required
                              for f in form.fields)

        if mode == _decide.DRY_RUN:
            decision = _decide.decide(_decide.Inputs(
                config=config, mode=mode, resolution=resolution,
                posting_dir=_posting_dir(sources), signals_before=signals_before,
                signals_at_submit=signals_before, answer_writers=writers,
                prior_submit_attempts=db.submit_attempts(conn, posting_id),
                current_status=db.current_statuses(conn).get(posting_id),
                recent_auto_submits=_recent(conn), now=now))
            outcome.decision = "dry_run"
            outcome.reasons = decision.reasons
            outcome.report = {"ats": ats, "url": url, "form": _form_summary(form),
                              "fields": _resolution_report(resolution),
                              "notes": notes, "signals_before": list(signals_before),
                              "would_decide": decision.outcome,
                              "letter_required": letter_required}
            _screenshot(page, shots, "dry-run", outcome)
            return

        report = _fill.fill_form(page, form, resolution, screenshot_dir=shots, tag="filled")
        outcome.screenshots.extend(report.screenshots)
        signals_now = tuple(_fill.collect_signals(page).kinds)
        decision = _decide.decide(_decide.Inputs(
            config=config, mode=mode, resolution=resolution,
            unverified=tuple(f.key for f in report.unverified),
            posting_dir=_posting_dir(sources), signals_before=signals_before,
            signals_at_submit=signals_now, answer_writers=writers,
            prior_submit_attempts=db.submit_attempts(conn, posting_id),
            current_status=db.current_statuses(conn).get(posting_id),
            recent_auto_submits=_recent(conn), now=now,
            fill_error=report.error if not report.form_found else None))
        outcome.reasons = decision.reasons
        outcome.report = {**report.to_dict(), "form": _form_summary(form),
                          "notes": notes, "signals_at_submit": list(signals_now),
                          "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail}
                                     for c in decision.checks],
                          "letter_required": letter_required}

        if not report.form_found:
            outcome.decision = "error"
            outcome.error = report.error
            return

        if decision.auto_submit:
            result = _submit.submit_once(
                page, ats, confirmation_timeout=config.browser.confirmation_timeout,
                screenshot_dir=shots, signals_before=signals_before,
                before_click=lambda: db.mark_submit_clicked(conn, outcome.run_id))
            outcome.screenshots.extend(result.screenshots)
            outcome.report["submit"] = {"outcome": result.outcome, "clicked": result.clicked,
                                        "detail": result.detail,
                                        "signals_at_submit": list(result.signals_at_submit)}
            if not result.clicked:
                outcome.decision = "review"
                outcome.reasons = outcome.reasons + [f"not clicked: {result.detail}"]
                outcome.status_recorded = _advance(conn, posting_id, "ready_for_review", letter_required)
                return
            outcome.decision = "auto_submit"
            outcome.submit_outcome = result.outcome
            if result.outcome == _submit.CONFIRMED:
                outcome.status_recorded = _advance(conn, posting_id, "applied", letter_required)
            else:
                outcome.status_recorded = _advance(conn, posting_id, "submit_unconfirmed", letter_required)
            return

        outcome.decision = "review"
        outcome.status_recorded = _advance(conn, posting_id, "ready_for_review", letter_required)


def _advance(conn, posting_id: int, status: str, letter_required: bool) -> str:
    out = tools.advance_status(conn, posting_id, status,
                               letter_required=letter_required, source="agent")
    return out["status"]


def _posting_dir(sources: _resolve.Sources) -> Path | None:
    for path in (sources.resume_path, sources.letter_pdf):
        if path is not None:
            return Path(path).parent
    return None


def _recent(conn) -> tuple[str, ...]:
    since = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    return tuple(r["started_at"] for r in db.auto_submits_since(conn, since))


def _form_summary(form: _spec.FormSpec) -> dict:
    return {"ats": form.ats, "job_id": form.job_id, "company": form.company,
            "title": form.title, "fields": len(form.fields),
            "required": len(form.required), "notes": list(form.notes),
            "bot_check_site_key": form.bot_check_site_key}


def _screenshot(page, directory: Path, name: str, outcome: RunOutcome) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        outcome.screenshots.append(str(path))
    except Exception:                                        # noqa: BLE001
        pass


def report_text(outcome: RunOutcome) -> str:
    """The run, for a terminal."""
    lines = [f"run {outcome.run_id} on posting {outcome.posting_id}: "
             f"{outcome.mode} -> {outcome.decision}"
             + (f" ({outcome.submit_outcome})" if outcome.submit_outcome else "")]
    if outcome.form_url:
        lines.append(f"  page: {outcome.form_url}")
    if outcome.error:
        lines.append(f"  error: {outcome.error}")
    for reason in outcome.reasons:
        lines.append(f"  - {reason}")
    for f in outcome.report.get("fields", []):
        status = f.get("status")
        src = f.get("source") or "-"
        lines.append(f"  {status:>16}  {f.get('key')}  [{f.get('action')} from {src}]"
                     + (f"  {f.get('note')}" if f.get("note") else ""))
    for note in outcome.report.get("notes", []):
        lines.append(f"  note: {note}")
    if outcome.status_recorded:
        lines.append(f"  status recorded: {outcome.status_recorded} (source=agent)")
    for shot in outcome.screenshots:
        lines.append(f"  screenshot: {shot}")
    return "\n".join(lines)
