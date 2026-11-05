"""The web workbench. TestClient against create_app, no server, no browser.

As with test_mcp_server.py, the tests that matter at this layer are not the
happy paths — the tool bodies every route calls are covered below. They are:

  * the route allowlist, which fails when a route is ADDED, and
  * the two things the web layer owns that the tool bodies do not: the
    localhost-only middleware, and the one write of its own, the .sent.md
    editor.

A temp FILE database, not ":memory:": `tools._conn` opens a fresh connection
per request, and an in-memory database would be empty on every one.

Nothing here reads bullets.yaml, master_profile.md, answers.yaml or .env. The
tools that need them are stubbed, so this file passes on a clean checkout.
"""
import os
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from jobpipe import db, mcp_server as tools, pipeline_tailor, states, web
from jobpipe.normalize import Posting

SAME_ORIGIN = {"Origin": "http://testserver"}

MINIMAL_STATES = """\
states:
  - id: not_applied
    label: Not applied
    aliases: []
    terminal: false
  - id: applied
    label: Applied
    aliases: []
    terminal: false
  - id: interview
    label: Interview
    aliases: []
    terminal: false
  - id: rejected
    label: Rejected
    aliases: []
    terminal: true
"""


@pytest.fixture(autouse=True)
def states_file(tmp_path, monkeypatch):
    """config/states.yml is committed, but if a checkout lacks it the status
    taxonomy comes from here rather than from a fake config in the tree."""
    if states.DEFAULT_PATH.is_file():
        return
    path = tmp_path / "states.yml"
    path.write_text(MINIMAL_STATES, encoding="utf-8")
    for name in ("load_states", "normalise", "labels", "is_regression"):
        original = getattr(states, name)
        monkeypatch.setattr(states, name,
                            lambda *a, _f=original, **k: _f(*a, path=path, **k))


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "jobpipe.db"
    connection = db.connect(str(path))
    db.init_schema(connection)
    connection.close()
    return str(path)


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def client(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_tier_map", lambda: {})
    # Application folders under the temp dir, never under the real output/.
    monkeypatch.setattr(pipeline_tailor, "OUTPUT_ROOT", tmp_path / "out")
    # The session worker's files under the temp dir, never under output/.
    return TestClient(web.create_app(db_path, session_dir=tmp_path / "session"))


def seed(conn, company="Abridge", title="AI Engineer", source_id="1",
         description="Build LLM pipelines.", apply_score=8,
         status="not_applied"):
    posting = Posting(ats_type="greenhouse", source_id=source_id,
                      company=company, title=title, location="NYC",
                      remote_flag=False, description=description,
                      url=f"https://x/{source_id}", posted_at=None)
    pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
    db.set_stage_a(conn, pid, True, "passed")
    db.insert_score(conn, pid, {
        "skills_match": 8, "seniority_fit": 8, "domain_relevance": 8,
        "overall": 8, "apply_score": apply_score, "apply_reason": "fit",
        "dealbreakers": [], "reasoning": "r"}, model="gemini-2.5-flash")
    if status:
        db.record_status(conn, pid, status)
    conn.commit()
    return pid


def letter(conn, pid, tmp_path, *, ok=1, content="Dear Team,\n\nBody.\n"):
    """A cover letter draft on disk and on record, under the temp OUTPUT_ROOT
    the client fixture points at."""
    row = db.get_posting(conn, pid)
    folder = pipeline_tailor.application_dir(row, tmp_path / "out")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cover_letter.md"
    path.write_text(content, encoding="utf-8", newline="")
    doc = db.insert_document(conn, pid, "cover_letter",
                             generated_path=str(path), content=content,
                             model="claude-mcp")
    if ok is not None:
        conn.execute("UPDATE documents SET grounding_ok = ?, "
                     "grounding_accuracy = ?, grounding_claims = ?, "
                     "grounding_checked_at = ? WHERE id = ?",
                     (ok, 1.0 if ok else 0.5,
                      '[{"claim": "x", "supported": "partial", "source": ""}]',
                      db.utcnow(), doc))
    conn.commit()
    return doc, path


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

class TestTheAllowlist:
    """The full set, not "no /submit exists": a denylist only catches the
    dangers somebody already thought of, while an allowlist fails on anything
    added — which is the failure that matters."""

    def test_the_live_routes_equal_the_allowlist(self, client):
        live = {(method, route.path) for route in client.app.routes
                if isinstance(route, APIRoute) for method in route.methods}
        assert live == set(web.ROUTE_ALLOWLIST)

    @pytest.mark.parametrize("forbidden", [
        "submit", "send", "mail", "score", "calibrate", "daily", "fetch",
        "ground/", "delete", "remove",
    ])
    def test_no_route_of_a_forbidden_shape_exists(self, forbidden):
        assert not [p for _, p in web.ROUTE_ALLOWLIST if forbidden in p]

    def test_every_write_is_a_post_under_a_posting_or_the_session(self):
        """A write lives under the posting it changes, or under /session/,
        whose four POSTs start the worker or hand it one word (Phase 7A).
        No corpus-wide action has a route: the session works only the
        postings Anish ticked."""
        for method, path in web.ROUTE_ALLOWLIST:
            if method != "GET":
                assert (path.startswith("/posting/{posting_id}/")
                        or path in ("/session/start", "/session/skip",
                                    "/session/pause", "/session/stop")), path


# ---------------------------------------------------------------------------
# Localhost only
# ---------------------------------------------------------------------------

class TestLocalhostOnly:
    """Binding to 127.0.0.1 stops neither a DNS-rebound Host nor a form POST
    from another site. The middleware does."""

    def test_a_non_loopback_host_is_refused(self, client, conn):
        pid = seed(conn)
        got = client.get(f"/posting/{pid}", headers={"Host": "evil.example"})
        assert got.status_code == 403
        assert "localhost only" in got.text

    def test_loopback_hosts_are_accepted(self, client, conn):
        seed(conn)
        for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765"):
            assert client.get("/", headers={"Host": host}).status_code == 200

    def test_a_cross_origin_post_is_refused(self, client, conn):
        pid = seed(conn)
        before = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
        got = client.post(f"/posting/{pid}/status", data={"status": "applied"},
                          headers={"Origin": "https://evil.example"})
        assert got.status_code == 403
        assert conn.execute(
            "SELECT COUNT(*) FROM status_events").fetchone()[0] == before

    def test_a_null_origin_is_refused(self, client, conn):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/status", data={"status": "applied"},
                          headers={"Origin": "null"})
        assert got.status_code == 403

    def test_a_cross_site_fetch_is_refused(self, client, conn):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/status", data={"status": "applied"},
                          headers={**SAME_ORIGIN, "Sec-Fetch-Site": "cross-site"})
        assert got.status_code == 403

    def test_a_same_origin_post_works(self, client, conn):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/status", data={"status": "applied"},
                          headers=SAME_ORIGIN, follow_redirects=False)
        assert got.status_code == 303
        assert got.headers["location"].startswith(f"/posting/{pid}?flash=")
        assert db.current_statuses(conn)[pid] == "applied"

    def test_a_get_needs_no_origin(self, client, conn):
        assert client.get("/queue").status_code == 200


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class TestPagesRender:
    def test_home(self, client, conn):
        pid = seed(conn)
        got = client.get("/")
        assert got.status_code == 200
        assert "Unscored backlog" in got.text
        assert f'href="/posting/{pid}"' in got.text

    def test_queue_and_its_tabs(self, client, conn):
        seed(conn, source_id="a")
        seed(conn, source_id="b", status="applied")
        got = client.get("/queue")
        assert got.status_code == 200
        assert "status=not_applied" in got.text
        assert "status=applied" in got.text
        assert got.text.count('<span class="count">(1)</span>') == 2
        assert "status=untracked" in got.text

    def test_every_queue_row_has_an_apply_link_to_its_own_page(
            self, client, conn):
        """Apply is a link to the board's page in a new tab — the one way
        this workbench can help someone apply. It is not a route, so the
        allowlist is unchanged, and it posts nothing."""
        seed(conn, source_id="a")
        seed(conn, source_id="b")
        got = client.get("/queue")
        for sid in ("a", "b"):
            assert (f'href="https://x/{sid}" target="_blank" '
                    f'rel="noopener noreferrer">Apply') in got.text

    def test_a_non_http_url_gets_no_apply_link(self, client, conn):
        posting = Posting(ats_type="greenhouse", source_id="js",
                          company="X", title="Y", location="", remote_flag=False,
                          description="", url="javascript:alert(1)",
                          posted_at=None)
        pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
        db.set_stage_a(conn, pid, True, "passed")
        db.record_status(conn, pid, "not_applied")
        conn.commit()
        got = client.get("/queue")
        assert "javascript:" not in got.text
        assert "no link" in got.text

    def test_queue_untracked(self, client, conn):
        pid = seed(conn, status=None)
        got = client.get("/queue?status=untracked&min_score=7")
        assert got.status_code == 200
        assert f'href="/posting/{pid}"' in got.text
        assert 'name="min_score"' in got.text

    def test_queue_unknown_status_is_a_400_not_a_500(self, client, conn):
        assert client.get("/queue?status=banana").status_code == 400

    def test_posting(self, client, conn, tmp_path):
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        db.insert_document(conn, pid, "short_answer", question="Why us?",
                           content="Because.", model="claude-mcp")
        conn.commit()
        got = client.get(f"/posting/{pid}")
        assert got.status_code == 200
        assert "Build LLM pipelines." in got.text
        assert "Untrusted third-party text" in got.text
        assert "Check grounding" in got.text
        assert "Why us?" in got.text
        assert "partial" in got.text          # the problem claim is shown
        assert 'href="https://x/1"' in got.text

    def test_posting_without_a_letter_or_resume(self, client, conn):
        pid = seed(conn)
        got = client.get(f"/posting/{pid}")
        assert got.status_code == 200
        assert "No letter drafted" in got.text
        assert "No resume rendered" in got.text

    def test_unknown_posting_is_a_404(self, client):
        assert client.get("/posting/99999").status_code == 404

    def test_answers_without_the_config_says_so(self, client, monkeypatch):
        """The config files are gitignored; without them the page explains
        rather than crashes. With them, it renders the view (stubbed here —
        the real one reads master_profile.md)."""
        got = client.get("/answers")
        assert got.status_code == 200
        assert "self-identification data" in got.text

    def test_answers_renders_a_view(self, client, monkeypatch):
        monkeypatch.setattr(tools, "form_answers", lambda: {
            "identity": {"name": "A. Person", "email": "a@example.com"},
            "education": {"degree": "MS"}, "work_authorization": {},
            "experience": [{"company": "X", "title": "Y"}],
            "self_identification": {"gender": "Man", "veteran": ""},
            "logistics": {"start_date": ""},
            "reusable_answers": {"why": {"answer": "Because.",
                                         "asked_as": ["Why?"]}},
            "leave_for_anish": ["self_identification.veteran"],
            "never_fill": ["salary or compensation expectations"],
        })
        got = client.get("/answers")
        assert got.status_code == 200
        assert "salary or compensation expectations" in got.text
        assert "self_identification.veteran" in got.text
        assert "A. Person" in got.text

    def test_measure_empty_shows_the_hint(self, client, conn):
        got = client.get("/measure")
        assert got.status_code == 200
        assert "COPY" in got.text

    def test_measure_with_a_pair(self, client, conn, tmp_path):
        pid = seed(conn)
        _, path = letter(conn, pid, tmp_path)
        (path.parent / "cover_letter.sent.md").write_text(
            "Dear Team,\n\nEdited body.\n", encoding="utf-8")
        got = client.get("/measure")
        assert got.status_code == 200
        assert "Per letter" in got.text
        assert f'href="/posting/{pid}"' in got.text


class TestUntrustedText:
    """Asserted on the injected string, not on `<script>` alone: base.html
    carries its own inline script for the theme switch."""

    def test_a_script_in_the_description_is_escaped(self, client, conn):
        pid = seed(conn, description="Apply now <script>alert(1)</script>")
        got = client.get(f"/posting/{pid}")
        assert "<script>alert(1)</script>" not in got.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in got.text

    def test_a_script_in_the_title_is_escaped_on_every_page(
            self, client, conn):
        pid = seed(conn, title="<script>x</script> Engineer")
        for url in ("/", "/queue", f"/posting/{pid}"):
            assert "<script>x</script>" not in client.get(url).text

    def test_a_non_http_url_is_not_linked(self, client, conn):
        posting = Posting(ats_type="greenhouse", source_id="js",
                          company="X", title="Y", location="", remote_flag=False,
                          description="", url="javascript:alert(1)",
                          posted_at=None)
        pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
        conn.commit()
        got = client.get(f"/posting/{pid}")
        assert got.status_code == 200
        assert "javascript:" not in got.text
        assert "no linkable URL" in got.text


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

class TestFiles:
    def test_serves_a_servable_file(self, client, conn, tmp_path):
        pid = seed(conn)
        _, path = letter(conn, pid, tmp_path)
        got = client.get(f"/posting/{pid}/file/cover_letter.md")
        assert got.status_code == 200
        assert got.text == "Dear Team,\n\nBody.\n"
        assert "inline" in got.headers["content-disposition"]

    def test_a_missing_servable_file_is_a_404(self, client, conn):
        pid = seed(conn)
        assert client.get(f"/posting/{pid}/file/resume.pdf").status_code == 404

    @pytest.mark.parametrize("name", [
        "notes.txt", "jobpipe.db", ".env", "../.env", "..%2F..%2F.env",
        "%2e%2e/%2e%2e/.env", "cover_letter.md%00.txt",
        "resume.pdf/../../.env",
    ])
    def test_anything_else_is_a_404(self, client, conn, tmp_path, name):
        """A bare `..` segment is not here: the client resolves it before the
        request leaves, so it would test httpx rather than the route."""
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        (tmp_path / ".env").write_text("SECRET=1")
        got = client.get(f"/posting/{pid}/file/{name}")
        assert got.status_code == 404
        assert "SECRET" not in got.text

    def test_unknown_posting_is_a_404(self, client):
        assert client.get("/posting/99999/file/resume.pdf").status_code == 404


# ---------------------------------------------------------------------------
# The editor: the one write the web layer owns
# ---------------------------------------------------------------------------

class TestSaveSentCopy:
    """Writes only cover_letter.sent.md, and only one that copy_for_editing
    produced for the CURRENT draft. The original is the baseline measure
    compares against; a stale copy is an edit of an earlier letter."""

    def test_refuses_when_there_is_no_letter(self, conn):
        pid = seed(conn)
        with pytest.raises(ToolError, match="no cover letter draft"):
            web.save_sent_copy(conn, pid, "edit")

    def test_refuses_a_missing_copy(self, conn, tmp_path):
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        with pytest.raises(ToolError, match="does not exist"):
            web.save_sent_copy(conn, pid, "edit")

    def test_refuses_a_stale_copy(self, conn, tmp_path):
        pid = seed(conn)
        _, path = letter(conn, pid, tmp_path)
        sent = path.parent / "cover_letter.sent.md"
        sent.write_text("an older edit", encoding="utf-8")
        os.utime(sent, (0, 0))
        with pytest.raises(ToolError, match="predates the current draft"):
            web.save_sent_copy(conn, pid, "edit")
        assert sent.read_text(encoding="utf-8") == "an older edit"

    def test_refuses_an_empty_body(self, conn, tmp_path):
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        tools.copy_for_editing(conn, pid)
        with pytest.raises(ToolError, match="empty"):
            web.save_sent_copy(conn, pid, "  \r\n \n")
        sent = path_of(conn, pid)
        assert sent.read_text(encoding="utf-8") == "Dear Team,\n\nBody.\n"

    def test_converts_crlf_to_lf(self, conn, tmp_path):
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        tools.copy_for_editing(conn, pid)
        out = web.save_sent_copy(conn, pid, "Dear Team,\r\n\r\nEdited.\r\n")
        assert Path(out["sent_path"]).read_bytes() == b"Dear Team,\n\nEdited.\n"
        assert out["words"] == 3

    def test_never_touches_the_original(self, conn, tmp_path):
        pid = seed(conn)
        _, path = letter(conn, pid, tmp_path)
        tools.copy_for_editing(conn, pid)
        before = path.read_bytes()
        web.save_sent_copy(conn, pid, "A completely different letter.")
        assert path.read_bytes() == before
        row = db.current_document(conn, pid, "cover_letter")
        assert row["content"] == "Dear Team,\n\nBody.\n"

    def test_reports_banned_phrases_without_refusing(self, conn, tmp_path):
        """His edit is his; the tool only says what it noticed."""
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        tools.copy_for_editing(conn, pid)
        out = web.save_sent_copy(conn, pid, "I am writing to apply.")
        assert out["banned_phrases"] == ["I am writing to apply"]

    def test_the_route_saves_and_shows_the_edit(self, client, conn, tmp_path):
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        tools.copy_for_editing(conn, pid)
        got = client.post(f"/posting/{pid}/sent",
                          data={"text": "Dear Team,\r\n\r\nFrom the browser.\r\n"},
                          headers=SAME_ORIGIN, follow_redirects=True)
        assert got.status_code == 200
        assert "Edit saved" in got.text
        assert "From the browser." in got.text
        assert path_of(conn, pid).read_bytes() == \
            b"Dear Team,\n\nFrom the browser.\n"

    def test_a_stale_copy_is_flagged_on_the_page(self, client, conn, tmp_path):
        pid = seed(conn)
        _, path = letter(conn, pid, tmp_path)
        sent = path.parent / "cover_letter.sent.md"
        sent.write_text("older", encoding="utf-8")
        os.utime(sent, (0, 0))
        assert "STALE" in client.get(f"/posting/{pid}").text


def path_of(conn, pid) -> Path:
    from jobpipe import measure
    row = db.current_document(conn, pid, "cover_letter")
    return measure.sent_path_for(row["generated_path"])


# ---------------------------------------------------------------------------
# Writes go through the tool bodies
# ---------------------------------------------------------------------------

class TestWritesAreToolBodies:
    def test_a_tool_error_is_an_error_flash_not_a_500(self, client, conn):
        """copy_for_editing on a posting with no letter refuses. The refusal
        is written for a reader, so it is shown verbatim."""
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/copy", headers=SAME_ORIGIN,
                          follow_redirects=True)
        assert got.status_code == 200
        assert "refused" in got.text
        assert "no cover letter to copy" in got.text

    def test_an_unchecked_draft_cannot_be_copied(self, client, conn, tmp_path):
        """The §5.2 gate, reached from a button."""
        pid = seed(conn)
        letter(conn, pid, tmp_path, ok=None)
        got = client.post(f"/posting/{pid}/copy", headers=SAME_ORIGIN,
                          follow_redirects=True)
        assert "no grounding verdict" in got.text
        assert not path_of(conn, pid).exists()

    def test_render_resume_calls_the_tool_with_the_form_values(
            self, client, conn, monkeypatch):
        calls = {}

        def fake(connection, posting_id, variant=None, posting_skills=None):
            calls.update(posting_id=posting_id, variant=variant,
                         posting_skills=posting_skills)
            return {"variant": variant, "dropped": ["spot-cv"],
                    "rendered": ["dist-distill"], "pages": 1}
        monkeypatch.setattr(tools, "render_resume", fake)
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/resume",
                          data={"variant": "swe_backend",
                                "posting_skills": " pytorch, vllm ,"},
                          headers=SAME_ORIGIN, follow_redirects=True)
        assert calls == {"posting_id": pid, "variant": "swe_backend",
                         "posting_skills": ["pytorch", "vllm"]}
        assert "Dropped to fit one page" in got.text
        assert "spot-cv" in got.text

    def test_render_resume_refusal_is_shown(self, client, conn, monkeypatch):
        def refuse(connection, posting_id, **kw):
            raise ToolError("cannot pick a variant for posting 1 on its own")
        monkeypatch.setattr(tools, "render_resume", refuse)
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/resume", data={"variant": ""},
                          headers=SAME_ORIGIN, follow_redirects=True)
        assert "cannot pick a variant" in got.text

    def test_check_grounding_passes_the_question(self, client, conn,
                                                 monkeypatch):
        calls = []

        def fake(connection, posting_id, question=None):
            calls.append((posting_id, question))
            return {"ok": True, "accuracy": 1.0, "problems": [],
                    "report": "grounding OK"}
        monkeypatch.setattr(tools, "check_grounding", fake)
        pid = seed(conn)
        client.post(f"/posting/{pid}/grounding", headers=SAME_ORIGIN)
        client.post(f"/posting/{pid}/grounding", data={"question": "Why us?"},
                    headers=SAME_ORIGIN)
        assert calls == [(pid, None), (pid, "Why us?")]

    def test_advance_status_records_source_web(self, client, conn):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/status",
                          data={"status": "applied", "letter_required": "yes"},
                          headers=SAME_ORIGIN, follow_redirects=True)
        assert "Status recorded" in got.text
        row = conn.execute("SELECT source, letter_required FROM status_events "
                           "WHERE posting_id = ? ORDER BY id DESC LIMIT 1",
                           (pid,)).fetchone()
        assert row["source"] == "web"
        assert row["letter_required"] == 1

    def test_letter_required_unset_stores_null(self, client, conn):
        pid = seed(conn)
        client.post(f"/posting/{pid}/status",
                    data={"status": "applied", "letter_required": ""},
                    headers=SAME_ORIGIN)
        row = conn.execute("SELECT letter_required FROM status_events "
                           "WHERE posting_id = ? ORDER BY id DESC LIMIT 1",
                           (pid,)).fetchone()
        assert row["letter_required"] is None

    def test_letter_pdf_reads_the_reviewed_copy_only(self, client, conn,
                                                     tmp_path):
        """prepare_letter refuses without a .sent.md. No PDF is rendered, so
        no Chromium is needed to see the refusal."""
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        got = client.post(f"/posting/{pid}/letter-pdf", headers=SAME_ORIGIN,
                          follow_redirects=True)
        assert "no reviewed copy" in got.text

    def test_a_flash_is_shown_once(self, client, conn):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/status", data={"status": "applied"},
                          headers=SAME_ORIGIN, follow_redirects=False)
        location = got.headers["location"]
        assert "Status recorded" in client.get(location).text
        assert "Status recorded" not in client.get(location).text


# ---------------------------------------------------------------------------
# Nothing on a page presses the buttons that do not exist
# ---------------------------------------------------------------------------

class TestNoForbiddenAction:
    """A cheap guard against a button creeping in. The MCP surface encodes
    stop-before-submit as absence; a page can only encode it as an action
    that never appears: no button, link, input or form action carries the
    word. The word itself may: `submit_unconfirmed` is a status (Phase 6),
    and the note that says never to click it is the point."""

    PAGES = ("/", "/queue", "/queue?status=untracked", "/posting/{pid}",
             "/answers", "/measure")

    def _seeded(self, client, conn, tmp_path):
        pid = seed(conn)
        letter(conn, pid, tmp_path)
        tools.copy_for_editing(conn, pid)
        db.insert_document(conn, pid, "short_answer", question="Why us?",
                           content="Because.", model="claude-mcp")
        conn.commit()
        return [(url.format(pid=pid), client.get(url.format(pid=pid)).text)
                for url in self.PAGES]

    def test_no_action_on_any_page_says_submit(self, client, conn, tmp_path):
        import re
        for url, body in self._seeded(client, conn, tmp_path):
            actions = re.findall(r"<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>",
                                 body, flags=re.I | re.S)
            actions += re.findall(r'<input\b[^>]*\bvalue="([^"]*)"', body)
            actions += re.findall(r'\baction="([^"]*)"', body)
            actions += re.findall(r'\btype="(submit)"', body, flags=re.I)
            hits = [a for a in actions if "submit" in a.lower()]
            assert not hits, (url, hits)

    def test_no_page_body_says_score_now(self, client, conn, tmp_path):
        for url, body in self._seeded(client, conn, tmp_path):
            assert "score now" not in body.lower(), url


# ---------------------------------------------------------------------------
# Phase 6: the runner from the workbench
# ---------------------------------------------------------------------------

class TestApplyRunnerRoutes:
    """The web layer starts the runner as a child and shows its record. It
    never drives a browser, never passes --auto, never touches apply.yaml."""

    class FakeStdin:
        def __init__(self):
            self.written = b""
            self.closed = False

        def write(self, data):
            self.written += data

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self):
            self.stdin = TestApplyRunnerRoutes.FakeStdin()
            self.returncode = None

        def poll(self):
            return self.returncode

    @pytest.fixture
    def launched(self, client, monkeypatch):
        calls = []

        def fake_launch(posting_id, mode):
            calls.append((posting_id, mode))
            return self.FakeProc()
        monkeypatch.setattr(client.app.state, "launch", fake_launch)
        monkeypatch.setattr(web, "_RUNS", {})
        return calls

    def test_the_allowlist_has_no_auto_and_no_submit(self):
        paths = [p for _, p in web.ROUTE_ALLOWLIST]
        assert "/posting/{posting_id}/fill" in paths
        assert not [p for p in paths if "auto" in p or "submit" in p or "apply.yaml" in p]

    def test_fill_starts_a_review_run_by_default(self, client, conn, launched):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/fill", headers=SAME_ORIGIN, follow_redirects=True)
        assert launched == [(pid, "review")]
        assert "Run started" in got.text

    def test_fill_accepts_dry_run_and_nothing_else(self, client, conn, launched):
        pid = seed(conn)
        client.post(f"/posting/{pid}/fill", data={"mode": "dry_run"}, headers=SAME_ORIGIN)
        got = client.post(f"/posting/{pid}/fill", data={"mode": "auto"}, headers=SAME_ORIGIN,
                          follow_redirects=True)
        assert launched == [(pid, "dry_run")]
        assert "never a page" in got.text

    def test_a_second_fill_while_one_is_open_is_refused(self, client, conn, launched):
        pid = seed(conn)
        client.post(f"/posting/{pid}/fill", headers=SAME_ORIGIN)
        got = client.post(f"/posting/{pid}/fill", headers=SAME_ORIGIN, follow_redirects=True)
        assert len(launched) == 1 and "still open" in got.text

    def test_close_sends_enter_to_the_runner(self, client, conn, launched):
        pid = seed(conn)
        client.post(f"/posting/{pid}/fill", headers=SAME_ORIGIN)
        proc = web._RUNS[pid]
        got = client.post(f"/posting/{pid}/fill/close", headers=SAME_ORIGIN, follow_redirects=True)
        assert "Review window closed" in got.text
        assert proc.stdin.written == b"\n" and proc.stdin.closed
        assert pid not in web._RUNS

    def test_fill_on_an_unknown_posting_is_a_404(self, client, launched):
        assert client.post("/posting/99999/fill", headers=SAME_ORIGIN).status_code == 404

    def test_fill_is_refused_cross_origin(self, client, conn, launched):
        pid = seed(conn)
        got = client.post(f"/posting/{pid}/fill", headers={"Origin": "https://evil.example"})
        assert got.status_code == 403 and launched == []

    def test_the_posting_page_shows_the_last_run(self, client, conn, tmp_path):
        pid = seed(conn)
        shot = tmp_path / "filled.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        run = db.start_apply_run(conn, pid, "review", ats="greenhouse")
        db.finish_apply_run(conn, run, decision="review",
                            reasons=["enabled: this run is review, not --auto",
                                     "no_blockers: 'What are your salary requirements?': salary: never filled"],
                            report={"fields": [{"key": "first_name", "label": "First Name", "action": "fill",
                                                "source": "profile:identity.first_name", "status": "ok", "note": ""},
                                               {"key": "question_1", "label": "What are your salary requirements?",
                                                "action": "blocker", "source": None, "status": "blocked",
                                                "note": "salary: never filled; left for Anish"}],
                                    "notes": ["no reviewed letter"], "signals_before": ["recaptcha"]},
                            screenshots=[str(shot)])
        got = client.get(f"/posting/{pid}")
        assert got.status_code == 200
        assert "Why not auto-submit" in got.text
        assert "salary: never filled" in got.text
        assert "bot check: recaptcha" in got.text
        assert f"/posting/{pid}/run/{run}/screenshot/filled.png" in got.text

    def test_a_screenshot_is_served_only_if_the_run_recorded_it(self, client, conn, tmp_path):
        pid = seed(conn)
        shot = tmp_path / "filled.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "secret.png").write_bytes(b"\x89PNG")
        run = db.start_apply_run(conn, pid, "review")
        db.finish_apply_run(conn, run, decision="review", screenshots=[str(shot)])
        assert client.get(f"/posting/{pid}/run/{run}/screenshot/filled.png").status_code == 200
        assert client.get(f"/posting/{pid}/run/{run}/screenshot/secret.png").status_code == 404
        assert client.get(f"/posting/{pid}/run/{run + 1}/screenshot/filled.png").status_code == 404

    def test_needs_attention_lists_what_the_runner_could_not_finish(self, client, conn):
        unconfirmed = seed(conn, source_id="a")
        fine = seed(conn, source_id="b")
        run = db.start_apply_run(conn, unconfirmed, "auto")
        db.finish_apply_run(conn, run, decision="auto_submit", submit_outcome="unconfirmed")
        tools.advance_status(conn, unconfirmed, "submit_unconfirmed", source="agent")
        run = db.start_apply_run(conn, fine, "review")
        db.finish_apply_run(conn, run, decision="review", reasons=["enabled: this run is review"])
        got = client.get("/queue?status=needs_attention")
        assert got.status_code == 200
        assert f'href="/posting/{unconfirmed}"' in got.text
        assert f'href="/posting/{fine}"' not in got.text
        assert "never click again" in got.text


# ---------------------------------------------------------------------------
# Phase 7A: the apply session routes
# ---------------------------------------------------------------------------

class TestApplySessionRoutes:
    """The worker is a child process; the routes start it, read its state
    file and write one word to its command file. The launcher is faked,
    so nothing here opens a browser or spends."""

    class FakeProc:
        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

    @pytest.fixture
    def launched(self, client, monkeypatch):
        calls = []

        def fake_launch(ids, limit, state_dir):
            calls.append((list(ids), limit, Path(state_dir)))
            return self.FakeProc()
        monkeypatch.setattr(client.app.state, "launch_session", fake_launch)
        monkeypatch.setattr(web, "_SESSION", {})
        return calls

    def write_state(self, client, **over):
        from jobpipe.apply import session
        state = session.SessionState("20261105T120000Z", "2026-11-05T12:00:00+00:00",
                                     status="running", cap=10, cost_usd=0.1234, drafted_for=1)
        for key, value in over.items():
            setattr(state, key, value)
        session.StateFile(client.app.state.session_dir).write(state)
        return state

    def test_the_six_routes_and_only_those(self):
        paths = {p for _, p in web.ROUTE_ALLOWLIST if p.startswith("/session")}
        assert paths == {"/session", "/session/state", "/session/start",
                         "/session/skip", "/session/pause", "/session/stop"}
        assert not [p for p in paths if "submit" in p or "auto" in p or "resume" in p]

    def test_the_panel_lists_the_queue_and_a_start_form(self, client, conn):
        pid = seed(conn)
        got = client.get("/session")
        assert got.status_code == 200
        assert f'name="ids" value="{pid}"' in got.text
        assert 'action="/session/start"' in got.text
        assert "http-equiv" not in got.text, "no refresh while nothing runs"
        assert "Nothing here" not in got.text

    def test_start_launches_the_worker_on_the_ticked_postings_in_queue_order(self, client, conn, launched):
        low = seed(conn, source_id="1", apply_score=6)
        high = seed(conn, source_id="2", apply_score=9)
        got = client.post("/session/start", data={"ids": [str(low), str(high)], "limit": "5"},
                          headers=SAME_ORIGIN, follow_redirects=True)
        assert "Session started" in got.text
        assert launched == [([low, high], 5, client.app.state.session_dir)]

    def test_start_refuses_nothing_ticked_and_postings_outside_the_queue(self, client, conn, launched):
        applied = seed(conn, status="applied")
        got = client.post("/session/start", data={}, headers=SAME_ORIGIN, follow_redirects=True)
        assert "no postings ticked" in got.text
        got = client.post("/session/start", data={"ids": str(applied)}, headers=SAME_ORIGIN,
                          follow_redirects=True)
        assert "in the session queue any more" in got.text
        assert launched == []

    def test_a_second_session_is_refused_while_one_runs(self, client, conn, launched):
        pid = seed(conn)
        client.post("/session/start", data={"ids": str(pid)}, headers=SAME_ORIGIN)
        got = client.post("/session/start", data={"ids": str(pid)}, headers=SAME_ORIGIN,
                          follow_redirects=True)
        assert "already running" in got.text and len(launched) == 1

    def test_the_buttons_write_one_word_and_only_while_the_worker_lives(self, client, conn, launched):
        from jobpipe.apply import session
        cmd = session.Commands(client.app.state.session_dir)
        got = client.post("/session/skip", headers=SAME_ORIGIN, follow_redirects=True)
        assert "no session running" in got.text and cmd.poll() is None
        pid = seed(conn)
        client.post("/session/start", data={"ids": str(pid)}, headers=SAME_ORIGIN)
        client.post("/session/skip", headers=SAME_ORIGIN)
        assert cmd.poll() == "skip"
        client.post("/session/pause", headers=SAME_ORIGIN)
        assert cmd.poll() == "pause"
        client.post("/session/pause", data={"action": "resume"}, headers=SAME_ORIGIN)
        assert cmd.poll() == "resume"
        client.post("/session/stop", headers=SAME_ORIGIN)
        assert cmd.poll() == "stop"

    def test_the_session_posts_are_refused_cross_origin(self, client, conn, launched):
        pid = seed(conn)
        for path, data in (("/session/start", {"ids": str(pid)}), ("/session/skip", {}),
                           ("/session/pause", {}), ("/session/stop", {})):
            got = client.post(path, data=data, headers={"Origin": "https://evil.example"})
            assert got.status_code == 403, path
        assert launched == []

    def test_the_state_route_and_the_panel_show_the_worker_file(self, client, conn, launched):
        from jobpipe.apply import session
        pid = seed(conn)
        client.post("/session/start", data={"ids": str(pid)}, headers=SAME_ORIGIN)
        state = self.write_state(client, current=pid)
        state.postings.append(session.PostingRecord(pid, "Abridge", "AI Engineer", outcome="working",
                                                    phase="drafts",
                                                    resume={"variant": "ai_ml", "why": "grid",
                                                            "pages": 1, "font_pt": 9.5,
                                                            "dropped": ["gt-remittance"],
                                                            "flagged": [{"bullet": "gt-remittance",
                                                                         "skills": ["postgresql"]}]},
                                                    drafts=[{"kind": "answer", "question": "Why?",
                                                             "verdict": "failed", "filled": False,
                                                             "reason": "left blank: [unsupported] led 40",
                                                             "cost": 0.01}]))
        session.StateFile(client.app.state.session_dir).write(state)
        got = client.get("/session/state")
        assert got.status_code == 200
        body = got.json()
        assert body["alive"] is True and body["state"]["cost_usd"] == 0.1234
        page = client.get("/session")
        assert "http-equiv=\"refresh\"" in page.text, "refreshes while the worker runs"
        assert "gt-remittance" in page.text and "postgresql" in page.text
        assert "left blank" in page.text and "FAILED" in page.text
        assert "$0.123" in page.text
        assert 'action="/session/skip"' in page.text and 'action="/session/stop"' in page.text

    def test_applied_shows_from_the_database_not_the_file(self, client, conn, launched):
        from jobpipe.apply import session
        pid = seed(conn)
        client.post("/session/start", data={"ids": str(pid)}, headers=SAME_ORIGIN)
        state = self.write_state(client)
        state.postings.append(session.PostingRecord(pid, "Abridge", "AI Engineer", outcome="applied"))
        session.StateFile(client.app.state.session_dir).write(state)
        tools.advance_status(conn, pid, "applied", source="agent")
        page = client.get("/session")
        assert "applied" in page.text
        queue = client.get("/queue?status=applied")
        assert f'href="/posting/{pid}"' in queue.text
        assert "Apply session running" in queue.text and "1 applied so far" in queue.text

    def test_a_dead_worker_with_a_running_file_is_shown_as_gone(self, client, conn):
        self.write_state(client, status="running")
        page = client.get("/session")
        assert "worker gone" in page.text
        assert 'action="/session/skip"' not in page.text
        assert "http-equiv" not in page.text
        assert client.get("/session/state").json()["state"]["stale"] is True

    def test_the_panel_never_offers_submit_or_auto(self, client, conn, launched):
        pid = seed(conn)
        client.post("/session/start", data={"ids": str(pid)}, headers=SAME_ORIGIN)
        self.write_state(client)
        text = client.get("/session").text.lower()
        for word in ("auto-submit", "--auto", "name=\"submit\"", "apply.yaml"):
            assert word not in text, word
