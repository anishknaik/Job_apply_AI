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
)

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


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="jobpipe", docs_url=None, redoc_url=None,
                  openapi_url=None)

    def conn():
        return tools._conn(db_path)

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
            else:
                try:
                    result = tools.list_queue(connection, status, limit)
                except ToolError as exc:
                    raise HTTPException(400, str(exc)) from exc
            counts = tools.status(connection)["by_status"]
        finally:
            connection.close()
        return page(request, "queue.html", result=result, counts=counts,
                    current=status, min_score=min_score)

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
        finally:
            connection.close()

        score = detail["score"] or {}
        suggested, why = variants.resolve(score.get("archetype"),
                                          score.get("domain"))
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
            suggested=suggested, why=why,
        )

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

    return app


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
