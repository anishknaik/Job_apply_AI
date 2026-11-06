"""jobpipe in a browser — a local workbench over the MCP tool bodies.

    python -m jobpipe.web            # http://127.0.0.1:8765

Thin for the same reason `mcp_server` is thin: every action here calls a
tool body from that module, so the refusals that protect a letter baseline,
the grounding gate and the supersede-never-overwrite rule are the SAME code
whether Claude or Anish presses the button. Logic that lived only behind a
route would be logic the suite and the MCP surface could not reach.

WHAT IS DELIBERATELY ABSENT, mirroring the MCP surface:

  * No submit and no send. There is nothing here that talks to an ATS or a
    mail server.
  * No score, calibrate, daily or fetch. They spend dollars and write
    corpus-wide; a button for them is a standing invitation to clear the
    backlog. They stay on the CLI, run on purpose.
  * No letter writing. Letters are drafted in a Claude session and checked by
    Gemini; this page shows them, gates them and holds the human edit.

`ROUTE_ALLOWLIST` is asserted against the live app by the tests, so a route
cannot appear by accident any more than an MCP tool can.

**Localhost only, and checked.** A page on any other site can POST a form to
127.0.0.1, and one of these buttons spends money (`check_grounding`) and two
append to the status log. So every request must carry a loopback Host header
(which also defeats DNS rebinding) and every POST must come from this
origin. Binding to 127.0.0.1 alone stops neither.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mcp.server.mcpserver.exceptions import ToolError

from . import (answers as _answers, db, letters, measure as _measure,
               mcp_server as tools, pipeline_tailor, states, variants)
from .apply import config as _apply_config, session as _session

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(REPO_ROOT / "templates" / "web"))

DEFAULT_PORT = 8765
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}

# Method and path of every route. An allowlist, not "no /submit exists": it
# fails on anything ADDED, which is the failure that matters.
ROUTE_ALLOWLIST = (
    ("GET", "/"),
    ("GET", "/queue"),
    ("GET", "/posting/{posting_id}"),
    ("GET", "/posting/{posting_id}/file/{name}"),
    ("GET", "/answers"),
    ("GET", "/measure"),
    ("POST", "/posting/{posting_id}/resume"),
    ("POST", "/posting/{posting_id}/grounding"),
    ("POST", "/posting/{posting_id}/copy"),
    ("POST", "/posting/{posting_id}/sent"),
    ("POST", "/posting/{posting_id}/letter-pdf"),
    ("POST", "/posting/{posting_id}/status"),
    # Phase 6. The runner is a separate process; these start it in review
    # or dry-run mode and show what it recorded. There is no route that
    # submits, none that passes --auto, and none that touches apply.yaml.
    ("POST", "/posting/{posting_id}/fill"),
    ("POST", "/posting/{posting_id}/fill/close"),
    ("GET", "/posting/{posting_id}/run/{run_id}/screenshot/{name}"),
    # Phase 7A, PHASE7_PLAN §2: the apply session. The worker is a child
    # process; these start it, read its state file, and write one word to
    # its command file. Nothing here submits, passes --auto, or touches
    # apply.yaml or learned_answers.yaml. /session/pause carries
    # action=pause|resume, so Resume needs no route of its own.
    ("GET", "/session"),
    ("GET", "/session/state"),
    ("POST", "/session/start"),
    ("POST", "/session/skip"),
    ("POST", "/session/pause"),
    ("POST", "/session/stop"),
)

# Runner processes this server started, by posting id. A review run keeps
# its browser window open until its stdin sees a newline, which is what
# /fill/close sends; the Popen is held here so that pipe stays open.
_RUNS: dict[int, subprocess.Popen] = {}
RUN_MODES = ("review", "dry_run")


def _launch_runner(posting_id: int, mode: str) -> subprocess.Popen:
    """`python -m jobpipe.apply <id> --review|--dry-run`, as a child.

    Never --auto: auto-submit is a CLI flag plus a gitignored file, and a
    page that could pass the flag could be POSTed by any site.
    """
    flag = "--review" if mode == "review" else "--dry-run"
    args = [sys.executable, "-m", "jobpipe.apply", str(posting_id), flag]
    log_dir = REPO_ROOT / "output" / "apply"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / f"web-run-{posting_id}.log", "ab")  # noqa: SIM115
    return subprocess.Popen(args, cwd=str(REPO_ROOT), stdin=subprocess.PIPE,
                            stdout=log, stderr=subprocess.STDOUT)

def _launch_session(ids: list[int], limit: int | None, state_dir: Path) -> subprocess.Popen:
    """`python -m jobpipe.apply.session --ids ... [--limit N]`, as a child.
    The worker fills in review mode only (session.py); there is no flag
    here that could make it do otherwise."""
    args = [sys.executable, "-m", "jobpipe.apply.session",
            "--ids", ",".join(str(i) for i in ids), "--state-dir", str(state_dir)]
    if limit:
        args += ["--limit", str(limit)]
    log_dir = REPO_ROOT / "output" / "apply"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "web-session.log", "ab")  # noqa: SIM115
    return subprocess.Popen(args, cwd=str(REPO_ROOT), stdout=log, stderr=subprocess.STDOUT)


# The session worker this server started, if any. One at a time.
_SESSION: dict[str, subprocess.Popen] = {}


def _session_alive() -> bool:
    proc = _SESSION.get("proc")
    return proc is not None and proc.poll() is None


# The only files a posting's folder serves. A name, never a path, so nothing
# outside output/applications/<slug>/ is reachable however the URL is shaped.
SERVABLE = {
    "resume.pdf": "application/pdf",
    "cover_letter.pdf": "application/pdf",
    "cover_letter.md": "text/plain; charset=utf-8",
    "cover_letter.sent.md": "text/plain; charset=utf-8",
}

# A result from one POST, shown once on the page it redirects to. In memory
# on purpose: it is a notice, not a record — the record is the database row
# the tool wrote.
_FLASH: dict[str, dict[str, Any]] = {}
_FLASH_LIMIT = 64


def _flash(kind: str, title: str, body: Any = None) -> str:
    token = secrets.token_urlsafe(8)
    if len(_FLASH) >= _FLASH_LIMIT:
        _FLASH.pop(next(iter(_FLASH)))
    _FLASH[token] = {"kind": kind, "title": title, "body": body}
    return token


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    return urlparse(value).hostname


def create_app(db_path: str | None = None, session_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="jobpipe", docs_url=None, redoc_url=None,
                  openapi_url=None)

    def conn():
        return tools._conn(db_path)

    # Where the session worker writes state.json and reads its command.
    # Under the runner's output_dir (gitignored), as apply.yaml says.
    app.state.session_dir = (Path(session_dir) if session_dir
                             else _session.default_state_dir(_apply_config.load()))

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host = _hostname(request.headers.get("host"))
        if host not in LOOPBACK and host != "testserver":
            return HTMLResponse("jobpipe answers on localhost only.", 403)
        if request.method != "GET":
            origin = request.headers.get("origin")
            site = request.headers.get("sec-fetch-site")
            # A browser sends Origin on every form POST. A mismatch, or a
            # fetch the browser itself labels cross-site, is another page
            # trying to press a button here.
            if origin is not None and (origin == "null" or
                                       _hostname(origin) != host):
                return HTMLResponse("cross-origin request refused.", 403)
            if site not in (None, "same-origin", "none"):
                return HTMLResponse("cross-site request refused.", 403)
        return await call_next(request)

    def page(request: Request, name: str, **context):
        token = request.query_params.get("flash")
        context["flash"] = _FLASH.pop(token, None) if token else None
        context["status_ids"] = [s.id for s in states.load_states()]
        context["nav"] = name.rsplit(".", 1)[0]
        return TEMPLATES.TemplateResponse(request, name, context)

    def back(posting_id: int, kind: str, title: str, body: Any = None):
        token = _flash(kind, title, body)
        return RedirectResponse(f"/posting/{posting_id}?flash={token}", 303)

    def run(posting_id: int, title: str, fn, *args, **kwargs):
        """Call a tool body; its refusal becomes the message on the page.

        A ToolError is an anticipated refusal written for a reader — the
        copy_for_editing gates, an unknown variant — and is shown verbatim.
        """
        connection = conn()
        try:
            result = fn(connection, *args, **kwargs)
        except ToolError as exc:
            return back(posting_id, "error", f"{title}: refused", str(exc))
        finally:
            connection.close()
        return back(posting_id, "ok", title, result)

    # -- reads -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        connection = conn()
        try:
            summary = tools.status(connection)
            corpus = tools.stats(connection)
            recent = connection.execute("""
                SELECT e.posting_id, e.status, e.noted_at, p.company, p.title
                FROM status_events e JOIN postings p ON p.id = e.posting_id
                ORDER BY e.id DESC LIMIT 12""").fetchall()
        finally:
            connection.close()
        return page(request, "home.html", summary=summary, corpus=corpus,
                    recent=recent)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, status: str = "not_applied",
              min_score: int = 7, limit: int = 100):
        connection = conn()
        try:
            if status == "untracked":
                result = _untracked(connection, min_score, limit)
            elif status == "needs_attention":
                result = _needs_attention(connection, limit)
            else:
                try:
                    result = tools.list_queue(connection, status, limit)
                except ToolError as exc:
                    raise HTTPException(400, str(exc)) from exc
            counts = tools.status(connection)["by_status"]
        finally:
            connection.close()
        # The Apply button on each row is a link to the board's own page,
        # opened in a new tab. Only an http(s) URL becomes one.
        for entry in result["postings"]:
            entry["apply_url"] = _safe_url(entry.get("url"))
        return page(request, "queue.html", result=result, counts=counts,
                    current=status, min_score=min_score,
                    live_session=_live_session(app.state.session_dir))

    @app.get("/posting/{posting_id}", response_class=HTMLResponse)
    def posting(request: Request, posting_id: int):
        connection = conn()
        try:
            try:
                detail = tools.get_posting(connection, posting_id)
            except ToolError as exc:
                raise HTTPException(404, str(exc)) from exc
            row = db.get_posting(connection, posting_id)
            folder = _folder(row)
            history = connection.execute(
                "SELECT status, previous, noted_at, source, letter_required "
                "FROM status_events WHERE posting_id = ? ORDER BY id",
                (posting_id,)).fetchall()
            resume = db.current_document(connection, posting_id, "resume")
            letter = db.current_document(connection, posting_id,
                                         "cover_letter")
            short = db.current_documents(connection, posting_id,
                                         _answers.DOC_TYPE)
            last_run = db.last_apply_run(connection, posting_id)
        finally:
            connection.close()

        score = detail["score"] or {}
        suggested, why = variants.resolve(score.get("archetype"),
                                          score.get("domain"),
                                          row["title"] or "")
        # The vocabulary an apply session renders with, prefilled so a
        # re-render from here starts where the session did. Free; a bank
        # that will not load leaves the box empty.
        try:
            from .bullets import load_bank, posting_vocabulary
            p = detail["posting"]
            vocabulary = list(posting_vocabulary(
                load_bank(), f"{p.get('title') or ''}\n{p.get('description') or ''}"))
        except Exception:                                    # noqa: BLE001
            vocabulary = []
        return page(
            request, "posting.html",
            p=detail["posting"], score=detail["score"],
            url=_safe_url(detail["posting"]["url"]),
            history=history,
            status=history[-1]["status"] if history else None,
            resume=_doc(resume), letter=_letter(letter),
            answers=[_doc(d) for d in short],
            files={name: (folder / name).exists() for name in SERVABLE},
            folder=folder,
            variants=sorted(variants.ORDERINGS),
            suggested=suggested, why=why, vocabulary=vocabulary,
            run=_run_view(last_run),
            run_alive=_alive(posting_id),
            run_modes=RUN_MODES,
        )

    @app.get("/posting/{posting_id}/run/{run_id}/screenshot/{name}")
    def run_screenshot(posting_id: int, run_id: int, name: str):
        """A screenshot the run recorded, by name, and only one it
        recorded: the stored list is the allowlist."""
        connection = conn()
        try:
            row = connection.execute(
                "SELECT screenshots FROM apply_runs WHERE id = ? AND posting_id = ?",
                (run_id, posting_id)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise HTTPException(404, "no such run")
        for recorded in json.loads(row["screenshots"] or "[]"):
            if Path(recorded).name == name and Path(recorded).is_file():
                return FileResponse(recorded, media_type="image/png",
                                    filename=name, content_disposition_type="inline")
        raise HTTPException(404, "not a screenshot of this run")

    @app.get("/posting/{posting_id}/file/{name}")
    def posting_file(posting_id: int, name: str):
        if name not in SERVABLE:
            raise HTTPException(404, "not a file this page serves")
        connection = conn()
        try:
            row = db.get_posting(connection, posting_id)
        finally:
            connection.close()
        if row is None:
            raise HTTPException(404, f"no posting with id {posting_id}")
        path = _folder(row) / name
        if not path.is_file():
            raise HTTPException(404, f"{name} has not been produced")
        # inline, so a PDF opens in the tab rather than downloading. The
        # filename is what makes FileResponse send the header at all.
        return FileResponse(path, media_type=SERVABLE[name], filename=name,
                            content_disposition_type="inline")

    @app.get("/answers", response_class=HTMLResponse)
    def answers(request: Request):
        try:
            view, error = tools.form_answers(), None
        except ToolError as exc:
            view, error = None, str(exc)
        return page(request, "answers.html", view=view, error=error)

    @app.get("/measure", response_class=HTMLResponse)
    def measure(request: Request):
        connection = conn()
        try:
            result = tools.measure(connection)
        finally:
            connection.close()
        return page(request, "measure.html", result=result,
                    slot_names=letters.BLOCK_NAMES)

    # -- writes: each one is a tool body ------------------------------------

    @app.post("/posting/{posting_id}/resume")
    def resume(posting_id: int, variant: str = Form(""),
               posting_skills: str = Form("")):
        skills = [s.strip() for s in posting_skills.split(",") if s.strip()]
        return run(posting_id, "Resume rendered", tools.render_resume,
                   posting_id, variant=variant or None,
                   posting_skills=skills)

    @app.post("/posting/{posting_id}/grounding")
    def grounding(posting_id: int, question: str = Form("")):
        return run(posting_id, "Grounding check", tools.check_grounding,
                   posting_id, question=question or None)

    @app.post("/posting/{posting_id}/copy")
    def copy(posting_id: int):
        return run(posting_id, "Copied for editing", tools.copy_for_editing,
                   posting_id)

    @app.post("/posting/{posting_id}/sent")
    def save_sent(posting_id: int, text: str = Form(...)):
        return run(posting_id, "Edit saved", save_sent_copy, posting_id,
                   text)

    @app.post("/posting/{posting_id}/letter-pdf")
    def letter_pdf(posting_id: int):
        return run(posting_id, "Letter PDF", _letter_pdf, posting_id)

    @app.post("/posting/{posting_id}/status")
    def advance(posting_id: int, status: str = Form(...),
                letter_required: str = Form("")):
        flag = {"yes": True, "no": False}.get(letter_required)
        return run(posting_id, "Status recorded", tools.advance_status,
                   posting_id, status, letter_required=flag, source="web")

    @app.post("/posting/{posting_id}/fill")
    def fill(posting_id: int, mode: str = Form("review")):
        """Start the runner on this posting, review or dry run. It records
        its own apply_runs row; reload the page to see it."""
        if mode not in RUN_MODES:
            return back(posting_id, "error", "Run not started",
                        f"mode must be one of {RUN_MODES}; --auto is never a page")
        connection = conn()
        try:
            if db.get_posting(connection, posting_id) is None:
                raise HTTPException(404, f"no posting with id {posting_id}")
        finally:
            connection.close()
        if _alive(posting_id):
            return back(posting_id, "error", "Run not started",
                        "a run on this posting is still open; close its window first")
        try:
            _RUNS[posting_id] = app.state.launch(posting_id, mode)
        except OSError as exc:
            return back(posting_id, "error", "Run not started", str(exc))
        return back(posting_id, "ok", "Run started",
                    {"mode": mode, "next": "the runner opens its own browser; "
                     "reload this page when it finishes to see the report"})

    @app.post("/posting/{posting_id}/fill/close")
    def fill_close(posting_id: int):
        proc = _RUNS.pop(posting_id, None)
        if proc is None or proc.poll() is not None:
            return back(posting_id, "ok", "No open run", "nothing to close")
        try:
            if proc.stdin:
                proc.stdin.write(b"\n")
                proc.stdin.close()
        except OSError:
            pass
        return back(posting_id, "ok", "Review window closed")

    # -- the apply session (Phase 7A) ----------------------------------------

    def session_view():
        state = _session.StateFile(app.state.session_dir).read()
        alive = _session_alive()
        if state and state.get("status") in (_session.RUNNING, _session.PAUSED,
                                             _session.STARTING) and not alive:
            # The worker is gone (killed, crashed, or started by an earlier
            # server) but its file says running: say so rather than show
            # buttons that would write commands nobody reads.
            state = dict(state, stale=True)
        return state, alive

    def session_back(kind: str, title: str, body: Any = None):
        token = _flash(kind, title, body)
        return RedirectResponse(f"/session?flash={token}", 303)

    @app.get("/session", response_class=HTMLResponse)
    def session_panel(request: Request):
        state, alive = session_view()
        connection = conn()
        try:
            queue_rows = _session.select_queue(connection, tier_map=tools._tier_map())
            statuses = db.current_statuses(connection)
        finally:
            connection.close()
        for entry in queue_rows:
            entry["apply_url"] = _safe_url(entry.get("url"))
        if state:
            for rec in state.get("postings", []):
                rec["status"] = statuses.get(rec["id"])
        cap = _session.max_drafts_per_session()
        return page(request, "session.html", state=state, alive=alive,
                    queue=queue_rows, cap=cap, days=_session.QUEUE_DAYS,
                    draft_model=_draft_model(),
                    refresh=bool(state) and alive and not state.get("stale"))

    @app.get("/session/state")
    def session_state():
        from fastapi.responses import JSONResponse
        state, alive = session_view()
        return JSONResponse({"alive": alive, "state": state})

    @app.post("/session/start")
    def session_start(ids: list[int] = Form([]), limit: str = Form("")):
        """Start the worker on the ticked postings. The click is the ask:
        this is the one place drafting spend starts, and it is Anish's."""
        if _session_alive():
            return session_back("error", "Session not started",
                                "a session is already running; stop it first")
        if not ids:
            return session_back("error", "Session not started", "no postings ticked")
        cap = _session.max_drafts_per_session()
        try:
            n = int(limit) if str(limit).strip() else None
        except ValueError:
            return session_back("error", "Session not started", f"limit must be a number, not {limit!r}")
        connection = conn()
        try:
            allowed = {e["id"] for e in _session.select_queue(connection)}
        finally:
            connection.close()
        chosen = [i for i in ids if i in allowed]
        if not chosen:
            return session_back("error", "Session not started",
                                "none of the ticked postings is in the session queue any more")
        if n is not None and n > 0:
            chosen = chosen[:n]
        # Clear a previous session's leftover command so the new worker
        # does not read a stale Stop the moment it starts.
        _session.Commands(app.state.session_dir).clear()
        try:
            _SESSION["proc"] = app.state.launch_session(chosen, n, app.state.session_dir)
        except OSError as exc:
            return session_back("error", "Session not started", str(exc))
        return session_back("ok", "Session started",
                            {"postings": len(chosen), "drafting cap": f"{cap} postings",
                             "next": "the worker opens its own browser for each form; this page "
                                     "refreshes itself while it runs"})

    def command(word: str, title: str):
        if not _session_alive():
            return session_back("error", f"{title}: no session running",
                                "nothing to send it to")
        _session.Commands(app.state.session_dir).send(word)
        return session_back("ok", title)

    @app.post("/session/skip")
    def session_skip():
        return command(_session.SKIP, "Skip sent: the current posting is left as it is")

    @app.post("/session/pause")
    def session_pause(action: str = Form("pause")):
        if action == "resume":
            return command(_session.RESUME, "Resume sent")
        return command(_session.PAUSE, "Pause sent: the session stops after the current posting")

    @app.post("/session/stop")
    def session_stop():
        return command(_session.STOP, "Stop sent: the session ends after the current step")

    app.state.launch = _launch_runner
    app.state.launch_session = _launch_session
    return app


def _draft_model() -> str:
    """Who writes the prose, for the panel. Claude in Claude Code by default
    (2026-11-06); the drafting model only when JOBPIPE_SESSION_DRAFTING=gemini."""
    from .apply import draft, session as _session
    if not _session.gemini_drafting():
        return "Claude (written in Claude Code before the session)"
    return draft.draft_model()


def _live_session(session_dir: Path) -> dict | None:
    """The running session, summarised for the queue page, or None."""
    state = _session.StateFile(session_dir).read()
    if not state or not _session_alive():
        return None
    if state.get("status") not in (_session.RUNNING, _session.PAUSED, _session.STARTING):
        return None
    postings = state.get("postings", [])
    return {"status": state["status"],
            "applied": sum(1 for r in postings if r.get("outcome") == _session.APPLIED),
            "remaining": sum(1 for r in postings if r.get("outcome") in (_session.PENDING,
                                                                          _session.WORKING))}


def _alive(posting_id: int) -> bool:
    proc = _RUNS.get(posting_id)
    return proc is not None and proc.poll() is None


# ---------------------------------------------------------------------------
# Helpers. Plain functions taking a connection, like the tool bodies, so the
# suite calls them directly.
# ---------------------------------------------------------------------------

def save_sent_copy(conn, posting_id: int, text: str) -> dict[str, Any]:
    """Save Anish's edit of the letter into cover_letter.sent.md.

    Writes ONLY the copy, and only a copy that exists and belongs to the
    current draft. The original cover_letter.md is the baseline `measure`
    compares against and is never written here; a missing copy means
    copy_for_editing has not run, and a stale one is an edit of an earlier
    draft that measure would refuse — both refusals are the same ones the
    tools make, for the same reasons.
    """
    doc = db.current_document(conn, posting_id, "cover_letter")
    if doc is None or not doc["generated_path"]:
        raise ToolError(f"posting {posting_id} has no cover letter draft.")
    sent = _measure.sent_path_for(doc["generated_path"])
    if not sent.exists():
        raise ToolError(
            f"{sent} does not exist. Copy the draft for editing first — the "
            f"original is the baseline and is never edited in place.")
    if _measure._is_stale(sent, doc["generated_at"]):
        raise ToolError(
            f"{sent} predates the current draft (generated "
            f"{doc['generated_at']}). It is an edit of an earlier letter; "
            f"delete it deliberately and copy the current draft again.")
    # A textarea submits CRLF. Stored drafts are LF, and a copy that differs
    # from its original on every line defeats `diff` as a review of the edit.
    body = text.replace("\r\n", "\n")
    if not body.strip():
        raise ToolError("the edit is empty; nothing was saved.")
    sent.write_text(body, encoding="utf-8", newline="")
    return {"sent_path": str(sent), "words": len(body.split()),
            "banned_phrases": letters.banned_used(body) or None}


def _letter_pdf(conn, posting_id: int) -> dict[str, Any]:
    out = tools.prepare_letter(conn, posting_id, pdf=True)
    out.pop("text", None)        # it is on the page already, as the edit
    return out


def _folder(row) -> Path:
    """output/applications/<slug>, with OUTPUT_ROOT read at call time.

    `application_dir` binds the root as a default argument, so a test that
    points `pipeline_tailor.OUTPUT_ROOT` at a temp folder would otherwise
    still be served files from the real one.
    """
    return pipeline_tailor.application_dir(row, pipeline_tailor.OUTPUT_ROOT)


def _untracked(conn, min_score: int, limit: int) -> dict[str, Any]:
    """Scored postings no status event has touched yet, best first.

    `list_queue` reads the status log, so a posting nobody has looked at is
    invisible to it. This is the other half of the shortlist.
    """
    rows = conn.execute("""
        SELECT p.id, p.company, p.title, p.location, p.url, p.remote_flag,
               s.overall, s.apply_score, s.archetype, s.domain, s.scored_at
        FROM postings p JOIN scores s ON s.posting_id = p.id
        WHERE p.stage_a_passed = 1 AND s.apply_score >= ?
          AND p.id NOT IN (SELECT posting_id FROM status_events)
        ORDER BY s.apply_score DESC, s.overall DESC, p.last_seen_at DESC""",
                        (min_score,)).fetchall()
    tiers = tools._tier_map()
    postings = []
    for row in rows:
        entry = dict(row)
        entry["tier"] = tiers.get((entry["company"] or "").strip().lower())
        postings.append(entry)
    return {"status": "untracked", "count": len(postings),
            "returned": min(len(postings), limit),
            "postings": postings[:limit]}


def _needs_attention(conn, limit: int) -> dict[str, Any]:
    """Postings the runner could not finish on its own: a submit it could
    not confirm, a run that errored, or a run that stopped on blockers."""
    rows = conn.execute("""
        SELECT r.*, p.company, p.title, p.location, p.url, p.remote_flag,
               s.overall, s.apply_score, s.archetype, s.domain
        FROM apply_runs r
        JOIN postings p ON p.id = r.posting_id
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE r.id IN (SELECT MAX(id) FROM apply_runs GROUP BY posting_id)
        ORDER BY r.id DESC""").fetchall()
    current = db.current_statuses(conn)
    tiers = tools._tier_map()
    postings = []
    for row in rows:
        entry = dict(row)
        reasons = json.loads(entry.get("reasons") or "[]")
        why = []
        if current.get(entry["posting_id"]) == "submit_unconfirmed":
            why.append("submit unconfirmed: check the ATS by hand, never click again")
        if entry.get("decision") == "error":
            why.append(f"run error: {entry.get('error')}")
        if any(r.startswith("no_blockers") for r in reasons):
            why.append("blockers")
        if entry.get("submit_outcome") in ("error", "challenge", "unconfirmed", "clicked"):
            why.append(f"submit {entry['submit_outcome']}")
        if not why:
            continue
        entry["id"] = entry["posting_id"]
        entry["attention"] = "; ".join(why)
        entry["tier"] = tiers.get((entry["company"] or "").strip().lower())
        entry["apply_url"] = _safe_url(entry.get("url"))
        postings.append(entry)
    return {"status": "needs_attention", "count": len(postings),
            "returned": min(len(postings), limit), "postings": postings[:limit]}


def _run_view(row) -> dict[str, Any] | None:
    """An apply_runs row with its JSON columns decoded for the template."""
    if row is None:
        return None
    view = dict(row)
    for key in ("reasons", "screenshots"):
        try:
            view[key] = json.loads(view.get(key) or "[]")
        except ValueError:
            view[key] = []
    try:
        view["report"] = json.loads(view.get("report") or "{}")
    except ValueError:
        view["report"] = {}
    view["screenshot_names"] = [Path(s).name for s in view["screenshots"]]
    return view


def _safe_url(url: str | None) -> str | None:
    """Only http(s) becomes a link. The URL came off a job board."""
    if url and urlparse(url).scheme in ("http", "https"):
        return url
    return None


def _doc(row) -> dict[str, Any] | None:
    if row is None:
        return None
    doc = dict(row)
    for key in ("grounding_claims", "bullet_ids"):
        try:
            doc[key] = json.loads(doc[key]) if doc.get(key) else []
        except ValueError:
            doc[key] = []
    doc["problems"] = [c for c in doc["grounding_claims"]
                       if c.get("supported") in ("unsupported", "partial")]
    return doc


def _letter(row) -> dict[str, Any] | None:
    doc = _doc(row)
    if doc is None:
        return None
    doc["sent_text"] = None
    doc["sent_stale"] = False
    if doc.get("generated_path"):
        sent = _measure.sent_path_for(doc["generated_path"])
        doc["sent_path"] = str(sent)
        if sent.exists():
            doc["sent_text"] = sent.read_text(encoding="utf-8")
            doc["sent_stale"] = _measure._is_stale(sent, doc["generated_at"])
    doc["words"] = len((doc.get("content") or "").split())
    return doc


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="jobpipe-web")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=None)
    parser.add_argument("--no-reload", action="store_true",
                        help="do not restart the server when jobpipe/*.py "
                             "changes")
    args = parser.parse_args()

    # Every path the pipeline uses — output/applications, config/, the
    # database — is relative to the repo, as it is for the MCP server.
    os.chdir(REPO_ROOT)
    # .env before anything reads a setting: it carries JOBPIPE_MIN_FONT_PT,
    # without which every resume renders at the 10pt default (see
    # mcp_server.main), and the key check_grounding builds a provider from.
    from .cli import _load_env
    _load_env()

    if args.no_reload:
        # 127.0.0.1, never 0.0.0.0: this page holds self-ID answers and a
        # button that spends money.
        uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)
        return
    worker = [sys.executable, "-m", "jobpipe.web", "--no-reload",
              "--port", str(args.port)]
    if args.db:
        worker += ["--db", args.db]
    _supervise(worker)


def _python_mtimes() -> dict[Path, float]:
    return {p: p.stat().st_mtime
            for p in (REPO_ROOT / "jobpipe").rglob("*.py")}


def _supervise(worker: list[str], interval: float = 1.0) -> None:
    """Run the server in a child and restart it when jobpipe/*.py changes.

    On by default: the branch gets pulled while the page is open, and a
    server still running the old code reads like the pull failed. Templates
    need no watching; Jinja re-reads a changed one on the next request.

    Not uvicorn's --reload. On Windows it stops the old worker with a
    CTRL_C_EVENT, which never arrives when there is no console to deliver
    it, and the reload hangs with the old code still serving. terminate()
    is TerminateProcess, which always lands.
    """
    import subprocess
    import time

    seen = _python_mtimes()
    child = subprocess.Popen(worker)
    try:
        while True:
            time.sleep(interval)
            now = _python_mtimes()
            if now == seen:
                continue
            seen = now
            print("jobpipe.web: code changed, restarting", flush=True)
            child.terminate()
            child.wait()
            # A pull that breaks the import exits the child at once; the next
            # change starts it again rather than respawning in a tight loop.
            child = subprocess.Popen(worker)
    except KeyboardInterrupt:
        pass
    finally:
        child.terminate()
        child.wait()


if __name__ == "__main__":
    main()
