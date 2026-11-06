"""The apply session (Phase 7A): the queue, worked on a loop, with Anish
reviewing and submitting each form.

    python -m jobpipe.apply.session --ids 12,34,56 [--limit N] [--db ...]

Started by the workbench (`POST /session/start`) as a child process, and
only ever by Anish's click: drafting and the browser spend money and this
is the one place they run unasked-per-posting. For each posting, in order:

  1. liveness  - `runner.inspect` opens the form headless; gone -> the
                 posting is recorded `discarded` (source=agent) and skipped
  2. resume    - `render_resume` from the grid variant, with the bank's
                 skill tags the posting names as its vocabulary
                 (bullets.posting_vocabulary), so relevance promotes before
                 the one-page loop cuts. A dropped bullet carrying one of
                 those tags is flagged on the panel - flagged, not skipped.
                 No variant -> skipped, named
  3. drafts    - for each open-ended question the form asks with no stored,
                 grounding-passed answer, and for the letter when the form
                 has a letter field or an "anything else" box, Gemini
                 drafts (draft.py) -> save_* with the model recorded ->
                 check_grounding. Only a passing draft is filled; a failing
                 one is left blank with the verdict shown. Under the cap:
                 JOBPIPE_MAX_DRAFTS_PER_SESSION postings may be drafted for,
                 and past it this step refuses and the form is filled
                 without them
  4. fill      - `runner.run` in review mode, the browser left open
  5. review    - `review.wait`, ended by his submit (the form leaves the
                 page; the runner records applied, Phase 6), by Skip or
                 Stop from the panel, or by him closing the window, which
                 pauses the session

The worker writes `state.json` in its directory after every change and the
workbench polls it; the panel's buttons write one word to `command` in the
same directory, which the worker consumes. Both are plain files so this
works on Windows with no pipes and no signals, and so a workbench restart
loses nothing.

What this never does: submit (decide.py's rule stands, and every form
with a drafted answer fails it), pass --auto, touch apply.yaml or
learned_answers.yaml, or fill a draft that did not pass grounding.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from .. import answers as _answers, db, letters, mcp_server as tools
from . import decide as _decide, draft as _draft, resolve as _resolve
from . import review as _review, runner as _runner, spec as _spec
from .config import ApplyConfig

log = logging.getLogger(__name__)

STATE_FILE, COMMAND_FILE = "state.json", "command"
CAP_ENV, DEFAULT_CAP = "JOBPIPE_MAX_DRAFTS_PER_SESSION", 10
QUEUE_DAYS = 2
ATSES = (_spec.GREENHOUSE, _spec.ASHBY)        # what the Phase 6 runner fills

# Session statuses and per-posting outcomes, as the panel shows them.
STARTING, RUNNING, PAUSED, FINISHED, STOPPED, ERRORED = (
    "starting", "running", "paused", "finished", "stopped", "error")
PENDING, WORKING, APPLIED, CLOSED, SKIPPED, LEFT_OPEN, NEEDS_VARIANT, ERROR = (
    "pending", "working", "applied", "closed", "skipped", "ready_for_review",
    "needs_variant", "error")
SKIP, PAUSE, RESUME, STOP = "skip", "pause", "resume", "stop"
COMMANDS = (SKIP, PAUSE, RESUME, STOP)


# Who writes a session's open-ended answers and letters. Anish's decision,
# 2026-11-06: Claude, in a Claude Code session, before the workbench session
# runs - so by default the worker drafts nothing and spends nothing; a box with
# no stored, grounding-passed answer is left blank and named on the panel.
# "gemini" brings back Phase 7A's drafting by JOBPIPE_DRAFT_MODEL.
DRAFTING_ENV = "JOBPIPE_SESSION_DRAFTING"
CLAUDE_NOTE = ("no answer on file: write it in Claude Code (save_short_answer or "
               "save_cover_letter, then check_grounding) and re-run this posting")


def gemini_drafting() -> bool:
    return (os.environ.get(DRAFTING_ENV) or "").strip().lower() == "gemini"


def max_drafts_per_session() -> int:
    raw = (os.environ.get(CAP_ENV) or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_CAP
    except ValueError:
        value = DEFAULT_CAP
    return value if value > 0 else DEFAULT_CAP


def default_state_dir(config: ApplyConfig) -> Path:
    return Path(config.output_dir) / "session"


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

def select_queue(conn, *, days: int = QUEUE_DAYS, limit: int | None = None,
                 tier_map: dict[str, int] | None = None) -> list[dict]:
    """The ranked shortlist the session works: scored, Stage A passed (which
    keeps an India-lane-only role out, since that lane never passes Stage A),
    seen in the last `days`, on a board the runner fills, and with no status
    yet or `not_applied`. Highest apply score first."""
    since = db.utc_days_ago(days)
    rows = conn.execute(f"""
        SELECT p.id, p.company, p.title, p.location, p.url, p.ats_type, p.remote_flag,
               p.last_seen_at, s.apply_score, s.overall, s.archetype, s.domain
        FROM postings p JOIN scores s ON s.posting_id = p.id
        WHERE p.stage_a_passed = 1 AND p.last_seen_at >= ?
          AND p.ats_type IN ({', '.join('?' * len(ATSES))})
        ORDER BY s.apply_score DESC, s.overall DESC, p.last_seen_at DESC""",
                        (since, *ATSES)).fetchall()
    current = db.current_statuses(conn)
    tiers = tier_map or {}
    out = []
    for row in rows:
        status = current.get(row["id"])
        if status not in (None, "not_applied"):
            continue
        entry = dict(row)
        entry["status"] = status
        entry["tier"] = tiers.get((entry["company"] or "").strip().lower())
        out.append(entry)
    return out[:limit] if limit else out


# ---------------------------------------------------------------------------
# State and commands: two files
# ---------------------------------------------------------------------------

@dataclass
class PostingRecord:
    id: int
    company: str
    title: str
    outcome: str = PENDING
    phase: str | None = None
    note: str = ""
    liveness: dict = field(default_factory=dict)
    resume: dict = field(default_factory=dict)
    drafts: list = field(default_factory=list)
    fill: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    status: str | None = None          # the database's current status, read live


@dataclass
class SessionState:
    session_id: str
    started_at: str
    status: str = STARTING
    updated_at: str = ""
    pid: int | None = None
    current: int | None = None          # posting id being worked
    postings: list = field(default_factory=list)
    cap: int = DEFAULT_CAP
    drafted_for: int = 0                # postings drafted for, against the cap
    cost_usd: float = 0.0
    calls: int = 0
    pause_reason: str = ""
    error: str = ""
    finished_at: str = ""

    def record(self, posting_id: int) -> PostingRecord:
        return next(r for r in self.postings if r.id == posting_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateFile:
    """state.json, replaced whole on every write so a reader never sees a
    half-written file (os.replace is atomic on Windows and POSIX)."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / STATE_FILE

    def write(self, state: SessionState) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        state.updated_at = _now()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(state), indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


class Commands:
    """The one-word `command` file the panel writes. `poll` consumes it."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / COMMAND_FILE
        self.last: str | None = None

    def send(self, command: str) -> None:
        if command not in COMMANDS:
            raise ValueError(f"unknown command {command!r}; one of {COMMANDS}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(command, encoding="utf-8")
        os.replace(tmp, self.path)

    def poll(self) -> str | None:
        try:
            word = self.path.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return None
        try:
            self.path.unlink()
        except OSError:
            pass
        if word in COMMANDS:
            self.last = word
            return word
        return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


class _WaitLines:
    """What review.wait polls instead of stdin: Skip and Stop end the wait,
    Pause is remembered for after this posting, Resume is a no-op here."""

    def __init__(self, commands: Commands, session: "Session"):
        self.commands, self.session = commands, session
        self.ended_by: str | None = None
        self.eof = False

    def get(self):
        word = self.commands.poll()
        if word in (SKIP, STOP):
            self.ended_by = word
            return word + "\n"
        if word == PAUSE:
            self.session.pause_requested = True
        return None


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

class SessionError(RuntimeError):
    pass


@dataclass
class Tools:
    """Every outside call the loop makes, injectable so the suite runs the
    loop against doubles and the Playwright test runs it against the fake
    ATS pages. Defaults are the real things."""
    inspect: object = _runner.inspect
    run: object = _runner.run
    wait: object = _review.wait
    render_resume: object = tools.render_resume
    save_short_answer: object = tools.save_short_answer
    save_cover_letter: object = tools.save_cover_letter
    check_grounding: object = tools.check_grounding
    copy_for_editing: object = tools.copy_for_editing
    prepare_letter: object = tools.prepare_letter
    form_answers: object = tools.form_answers
    advance_status: object = tools.advance_status
    draft_answer: object = _draft.draft_answer
    draft_letter: object = _draft.draft_letter
    build_sources: object = _runner.build_sources


class Session:
    def __init__(self, conn_factory, posting_ids: list[int], config: ApplyConfig, *,
                 state_dir: Path | None = None, tools_: Tools | None = None,
                 provider_factory=None, bank_loader=None, profile_loader=None,
                 exemplars_loader=None, targets: list | None = None,
                 cap: int | None = None, poll: float = 2.0, run_kwargs: dict | None = None):
        self.conn_factory = conn_factory
        self.ids = list(posting_ids)
        self.config = config
        self.dir = Path(state_dir) if state_dir else default_state_dir(config)
        self.t = tools_ or Tools()
        self.provider_factory = provider_factory
        self.bank_loader = bank_loader
        self.profile_loader = profile_loader
        self.exemplars_loader = exemplars_loader
        self.targets = targets
        self.poll = poll
        self.run_kwargs = run_kwargs or {}
        self.state_file = StateFile(self.dir)
        self.commands = Commands(self.dir)
        self.pause_requested = False
        self.stop_requested = False
        self.state = SessionState(session_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                                  started_at=_now(), pid=os.getpid(),
                                  cap=cap or max_drafts_per_session())
        self._provider = self._bank = self._profile = self._exemplars = None
        self._answer_system = None
        self._exemplars_error = None

    # -- lazy, paid-for things ----------------------------------------------

    def provider(self):
        if self._provider is None:
            if self.provider_factory is None:
                from ..providers import get_provider
                self.provider_factory = get_provider
            self._provider = self.provider_factory()
        return self._provider

    def bank(self):
        if self._bank is None:
            if self.bank_loader is None:
                from .. import bullets
                self.bank_loader = bullets.load_bank
            self._bank = self.bank_loader()
        return self._bank

    def profile(self):
        if self._profile is None:
            if self.profile_loader is None:
                from .. import scoring
                self.profile_loader = scoring.load_profile
            self._profile = self.profile_loader()
        return self._profile

    def exemplars(self):
        if self._exemplars is None and self._exemplars_error is None:
            try:
                loader = self.exemplars_loader or letters.load_exemplars
                self._exemplars = loader()
            except Exception as exc:                         # noqa: BLE001
                self._exemplars_error = f"{type(exc).__name__}: {exc}"
        if self._exemplars is None:
            raise _draft.DraftError(f"voice exemplars unavailable: {self._exemplars_error}")
        return self._exemplars

    def answer_system(self):
        if self._answer_system is None:
            self._answer_system = _draft.build_answer_system_prompt(self.bank(), self.profile())
        return self._answer_system

    # -- bookkeeping ----------------------------------------------------------

    def save(self) -> None:
        self.state_file.write(self.state)

    def _refresh_statuses(self, conn) -> None:
        current = db.current_statuses(conn)
        for rec in self.state.postings:
            rec.status = current.get(rec.id)

    def _spend(self, model: str, usage: dict | None) -> float:
        cost = _draft.estimate_cost(model, usage or {})
        self.state.cost_usd = round(self.state.cost_usd + cost, 6)
        self.state.calls += 1
        return cost

    def _check_commands(self) -> None:
        word = self.commands.poll()
        if word == STOP:
            self.stop_requested = True
        elif word == PAUSE:
            self.pause_requested = True

    def _pause(self, reason: str) -> bool:
        """Block until Resume or Stop. Returns False when stopped."""
        self.pause_requested = False
        self.state.status, self.state.pause_reason = PAUSED, reason
        self.save()
        while True:
            word = self.commands.poll()
            if word == STOP:
                self.stop_requested = True
                return False
            if word in (RESUME, SKIP):
                self.state.status, self.state.pause_reason = RUNNING, ""
                self.save()
                return True
            time.sleep(self.poll)

    # -- the loop -------------------------------------------------------------

    def run(self) -> SessionState:
        conn = self.conn_factory()
        try:
            for pid in self.ids:
                row = db.get_posting(conn, pid)
                if row is None:
                    continue
                self.state.postings.append(PostingRecord(pid, row["company"], row["title"]))
            self._refresh_statuses(conn)
        finally:
            conn.close()
        self.commands.clear()
        self.state.status = RUNNING
        self.save()
        try:
            for rec in self.state.postings:
                self._check_commands()
                if self.stop_requested:
                    break
                if self.pause_requested and not self._pause("paused before the next posting"):
                    break
                self.state.current = rec.id
                while True:
                    self.work(rec)
                    if rec.outcome == LEFT_OPEN and not self.stop_requested:
                        # He closed the window without submitting: the
                        # session pauses, and Resume re-fills this posting.
                        if self._pause(f"{rec.company}: the review window was closed "
                                       f"before a submit; Resume re-fills it, Skip moves on"):
                            if self.commands.last == RESUME:
                                continue
                            rec.outcome, rec.note = SKIPPED, "skipped from the panel"
                    break
                self.state.current = None
                self.save()
                if self.stop_requested:
                    break
                if self.pause_requested and not self._pause("paused after a posting"):
                    break
            self.state.status = STOPPED if self.stop_requested else FINISHED
        except Exception as exc:                             # noqa: BLE001
            log.exception("apply session failed")
            self.state.status, self.state.error = ERRORED, f"{type(exc).__name__}: {exc}"[:500]
        self.state.finished_at = _now()
        self.save()
        return self.state

    # -- one posting ----------------------------------------------------------

    def work(self, rec: PostingRecord) -> None:
        conn = self.conn_factory()
        try:
            rec.outcome, rec.note = WORKING, ""
            self._refresh_statuses(conn)
            if rec.status == "applied":
                rec.outcome, rec.note = SKIPPED, "already applied (sync the sheet before a session)"
                return
            rec.phase = "liveness"
            self.save()
            found = self._liveness(conn, rec)
            if found is None:
                return
            rec.phase = "resume"
            self.save()
            if not self._resume(conn, rec):
                return
            rec.phase = "drafts"
            self.save()
            self._drafts(conn, rec, found.form)
            self._check_commands()
            if self.stop_requested:
                rec.outcome, rec.note = SKIPPED, "stopped before the fill"
                return
            rec.phase = "fill"
            self.save()
            self._fill_and_review(conn, rec, found)
        except Exception as exc:                             # noqa: BLE001
            log.exception("posting %s failed", rec.id)
            rec.outcome, rec.note = ERROR, f"{type(exc).__name__}: {exc}"[:400]
        finally:
            rec.phase = None
            self._refresh_statuses(conn)
            conn.close()
            self.save()

    def _liveness(self, conn, rec: PostingRecord):
        found = self.t.inspect(conn, rec.id, self.config, targets=self.targets,
                               **{k: v for k, v in self.run_kwargs.items()
                                  if k in ("fetch_json", "form_url", "browser_factory")})
        rec.liveness = {"alive": found.alive, "url": found.url, "reason": found.reason,
                        "signals": list(found.signals),
                        "fields": len(found.form.fields) if found.form else None}
        if found.alive:
            return found
        try:
            out = self.t.advance_status(conn, rec.id, "discarded", source="agent")
            rec.liveness["status_recorded"] = out["status"]
        except ToolError as exc:
            rec.liveness["status_recorded"] = f"not recorded: {exc}"
        rec.outcome, rec.note = CLOSED, f"form not found: {found.reason}"
        return None

    def _resume(self, conn, rec: PostingRecord) -> bool:
        vocabulary = self._posting_skills(conn, rec)
        try:
            out = self.t.render_resume(conn, rec.id, variant=None,
                                       posting_skills=list(vocabulary))
        except ToolError as exc:
            rec.resume = {"error": str(exc), "posting_skills": vocabulary}
            rec.outcome, rec.note = NEEDS_VARIANT, ("no variant resolves from the grid; "
                                                   "render one from the posting page")
            return False
        flagged = self._flagged(out["dropped"], vocabulary)
        rec.resume = {"variant": out["variant"], "why": out["why"], "path": out["path"],
                      "pages": out["pages"], "font_pt": out["font_pt"],
                      "rendered": out["rendered"], "dropped": out["dropped"],
                      "posting_skills": vocabulary, "flagged": flagged}
        # Flagged, not skipped (PHASE7_PLAN §2): the form is still filled, the
        # panel marks the row, and he decides in review whether to stop and
        # re-render from the posting page.
        return True

    def _posting_skills(self, conn, rec: PostingRecord) -> dict[str, int]:
        """The bank's specific skill tags the posting's title and text name,
        with counts, most-named first. Fed to render_resume so its relevance
        promotion runs, and kept for the panel. A bank that will not load
        renders without it (render_resume then fails on the same bank and
        says why), rather than stopping here."""
        from ..bullets import posting_vocabulary
        row = db.get_posting(conn, rec.id)
        if row is None:
            return {}
        try:
            bank = self.bank()
        except Exception as exc:                             # noqa: BLE001
            log.warning("posting %s: no vocabulary, bank not loaded (%s)", rec.id, exc)
            return {}
        return posting_vocabulary(bank, f"{row['title'] or ''}\n{row['description'] or ''}")

    def _flagged(self, dropped: list[str], vocabulary: dict[str, int]) -> list[dict]:
        """Dropped bullets carrying a skill the posting names."""
        from ..bullets import flag_dropped
        if not dropped or not vocabulary:
            return []
        return flag_dropped(self.bank(), dropped, vocabulary)

    # -- drafts ----------------------------------------------------------------

    def _open_questions(self, conn, rec: PostingRecord, form) -> tuple[list, bool, dict]:
        """(open-ended fields with no usable stored answer, whether a letter
        is wanted, reusable answers by normalised question)."""
        sources, notes, _ = self.t.build_sources(
            conn, rec.id, form, answers_loader=self.t.form_answers,
            prepare_letter=self.t.prepare_letter, company=None)
        resolution = _resolve.resolve(form, sources)
        open_fields = []
        for item in resolution.items:
            f = item.field
            if f.kind not in (_spec.TEXT, _spec.TEXTAREA):
                continue
            if item.action not in (_resolve.BLOCKER, _resolve.BLANK):
                continue
            if item.meaning not in ("open_ended", "additional_info"):
                continue
            open_fields.append(f)
        wants_letter = any(_resolve.meaning_of(f) == "cover_letter" for f in form.fields) or any(
            _resolve.meaning_of(f) == "additional_info" and f.kind == _spec.TEXTAREA
            for f in form.fields)
        reusable = {}
        for entry in ((sources.answers or {}).get("reusable_answers") or {}).values():
            for asked in entry.get("asked_as") or []:
                reusable[_answers.normalise_question(asked)] = entry["answer"]
        return open_fields, wants_letter, reusable

    def _drafts(self, conn, rec: PostingRecord, form) -> None:
        open_fields, wants_letter, reusable = self._open_questions(conn, rec, form)
        rec.drafts = []
        letter_needed = wants_letter and not self._letter_ready(conn, rec)
        if not open_fields and not letter_needed:
            return
        if not gemini_drafting():
            rec.drafts.extend({"kind": "answer", "question": f.label, "key": f.key,
                               "filled": False, "verdict": None, "reason": CLAUDE_NOTE,
                               "model": None, "cost": 0.0} for f in open_fields)
            if letter_needed:
                rec.drafts.append({"kind": "letter", "question": "cover letter", "filled": False,
                                   "verdict": None, "reason": CLAUDE_NOTE,
                                   "model": None, "cost": 0.0})
            self.save()
            return
        if self.state.drafted_for >= self.state.cap:
            rec.drafts.append({"kind": "cap", "filled": False,
                               "reason": f"{self.state.cap} postings drafted for this session, "
                                         f"the cap ({CAP_ENV}); nothing drafted, the form is "
                                         f"filled without these"})
            return
        self.state.drafted_for += 1
        self.save()
        row = db.get_posting(conn, rec.id)
        for f in open_fields:
            rec.drafts.append(self._draft_answer(conn, row, f, reusable))
            self.save()
        if letter_needed:
            rec.drafts.append(self._draft_letter(conn, row))
            self.save()

    def _letter_ready(self, conn, rec: PostingRecord) -> bool:
        """A reviewed, grounding-passed letter is already on file."""
        try:
            self.t.prepare_letter(conn, rec.id)
            return True
        except ToolError:
            return False

    def _draft_answer(self, conn, row, f, reusable: dict) -> dict:
        question = f.label
        entry = {"kind": "answer", "question": question, "key": f.key, "filled": False,
                 "verdict": None, "reason": "", "model": None, "cost": 0.0}
        start = reusable.get(_answers.normalise_question(question))
        try:
            out = self.t.draft_answer(self.provider(), self.bank(), self.profile(), row, question,
                                      max_length=f.max_length, single_line=(f.kind == _spec.TEXT),
                                      reusable=start, system_prompt=self.answer_system())
        except (_draft.DraftError, Exception) as exc:        # noqa: BLE001
            entry["reason"] = f"not drafted: {exc}"[:400]
            return entry
        entry["model"] = out["model"]
        entry["cost"] = self._spend(out["model"], out["usage"])
        try:
            self.t.save_short_answer(conn, row["id"], question, out["answer"], model=out["model"])
        except ToolError as exc:
            entry["reason"] = f"not saved: {exc}"[:400]
            return entry
        return self._ground(conn, row, entry, question=question)

    def _draft_letter(self, conn, row) -> dict:
        entry = {"kind": "letter", "question": "cover letter", "filled": False,
                 "verdict": None, "reason": "", "model": None, "cost": 0.0}
        try:
            out = self.t.draft_letter(self.provider(), self.bank(), self.profile(), row,
                                      exemplars=self.exemplars())
        except (_draft.DraftError, Exception) as exc:        # noqa: BLE001
            entry["reason"] = f"not drafted: {exc}"[:400]
            return entry
        entry["model"] = out["model"]
        entry["cost"] = self._spend(out["model"], out["usage"])
        try:
            saved = self.t.save_cover_letter(conn, row["id"], **out["paragraphs"], model=out["model"])
        except ToolError as exc:
            entry["reason"] = f"not saved: {exc}"[:400]
            return entry
        entry["words"] = saved.get("words")
        entry = self._ground(conn, row, entry)
        if entry["verdict"] == "passed":
            # The copy prepare_letter reads. He reviews it in the form, not
            # in the file, so the copy is the draft itself; an existing or
            # stale copy is refused by the tool and the letter stays out.
            try:
                self.t.copy_for_editing(conn, row["id"])
                entry["filled"] = True
            except ToolError as exc:
                entry["filled"] = False
                entry["reason"] = f"passed but not released: {exc}"[:400]
        return entry

    def _ground(self, conn, row, entry: dict, question: str | None = None) -> dict:
        try:
            verdict = self.t.check_grounding(conn, row["id"], question=question)
        except ToolError as exc:
            entry["verdict"] = "unchecked"
            entry["reason"] = f"grounding did not complete: {exc}"[:400]
            return entry
        entry["cost"] += self._spend(verdict.get("model") or "gemini-2.5-flash-lite",
                                     verdict.get("usage"))
        entry["accuracy"] = verdict.get("accuracy")
        entry["claims"] = verdict.get("claims")
        if verdict.get("ok"):
            entry["verdict"] = "passed"
            entry["filled"] = question is not None    # a letter is released by the copy
        else:
            entry["verdict"] = "failed"
            entry["filled"] = False
            problems = verdict.get("problems") or []
            entry["reason"] = "left blank: " + "; ".join(
                f"[{p.get('supported')}] {p.get('claim')}" for p in problems)[:600]
        return entry

    # -- fill and review -------------------------------------------------------

    def _fill_and_review(self, conn, rec: PostingRecord, found) -> None:
        kwargs = {k: v for k, v in self.run_kwargs.items()
                  if k in ("fetch_json", "form_url", "browser_factory", "answers_loader",
                           "prepare_letter", "employers_loader", "learned_loader", "headless")}
        outcome = self.t.run(conn, rec.id, _decide.REVIEW_MODE, self.config,
                             targets=self.targets, keep_open=True, **kwargs)
        report = outcome.report or {}
        rec.fill = {"run_id": outcome.run_id, "decision": outcome.decision,
                    "error": outcome.error, "reasons": list(outcome.reasons),
                    "status_recorded": outcome.status_recorded,
                    "fields": [{"key": f.get("key"), "label": f.get("label"),
                                "status": f.get("status"), "source": f.get("source"),
                                "note": f.get("note")} for f in report.get("fields", [])],
                    "notes": list(report.get("notes", [])),
                    "signals": list(report.get("signals_before", [])),
                    "letter_required": bool(report.get("letter_required"))}
        if outcome.error or outcome.browser is None or outcome.review is None:
            rec.outcome, rec.note = ERROR, f"fill did not complete: {outcome.error}"
            if outcome.browser is not None:
                outcome.browser.close()
            return
        rec.phase = "review"
        self.save()
        letter_required = rec.fill["letter_required"]

        def record_applied(pid=rec.id):
            c = self.conn_factory()
            try:
                return self.t.advance_status(c, pid, "applied", letter_required=letter_required,
                                             source="agent")["status"]
            finally:
                c.close()

        outcome.review.record_applied = record_applied
        lines = _WaitLines(self.commands, self)
        try:
            result = self.t.wait(outcome.review, lines, poll=self.poll, end_when_form_gone=True)
        finally:
            outcome.browser.close()
        rec.review = {"ended_by": result.ended_by, "form_gone": result.form_gone,
                      "status_recorded": result.status_recorded,
                      "verdict": (result.evidence or {}).get("verdict"),
                      "learned": list(result.learned_recorded)}
        if lines.ended_by == STOP:
            self.stop_requested = True
        if result.status_recorded == "applied":
            rec.outcome, rec.note = APPLIED, "submitted by Anish; applied recorded by the runner"
        elif result.form_gone:
            rec.outcome = LEFT_OPEN
            rec.note = (f"the form left the page but the ATS showed "
                        f"{rec.review['verdict'] or 'nothing recognised'}; applied not recorded")
        elif lines.ended_by == SKIP:
            rec.outcome, rec.note = SKIPPED, "skipped from the panel"
        elif lines.ended_by == STOP:
            rec.outcome, rec.note = SKIPPED, "session stopped from the panel"
        else:
            rec.outcome = LEFT_OPEN
            rec.note = "the review window was closed before a submit"


# ---------------------------------------------------------------------------
# The child process
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m jobpipe.apply.session",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--ids", required=True, help="posting ids, comma-separated, in order")
    parser.add_argument("--limit", type=int, default=None, help="work at most this many")
    parser.add_argument("--db", default=None)
    parser.add_argument("--config", default=None, help="apply.yaml path")
    parser.add_argument("--state-dir", default=None)
    args = parser.parse_args(argv)

    os.chdir(Path(__file__).resolve().parent.parent.parent)
    from ..cli import _load_env
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from ..targets import load_targets
    from . import config as _config

    cfg = _config.load(args.config)
    ids = [int(x) for x in args.ids.split(",") if x.strip()]
    if args.limit:
        ids = ids[:max(args.limit, 0)]
    try:
        targets = load_targets()
    except Exception as exc:                                 # noqa: BLE001
        print(f"targets.csv not loaded ({exc}); Greenhouse tokens will come from posting URLs")
        targets = None

    session = Session(lambda: tools._conn(args.db), ids, cfg, state_dir=args.state_dir,
                      targets=targets)
    state = session.run()
    print(f"session {state.session_id}: {state.status}, "
          f"{sum(1 for r in state.postings if r.outcome == APPLIED)} applied, "
          f"${state.cost_usd:.3f} estimated")
    return 0 if state.status in (FINISHED, STOPPED) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
