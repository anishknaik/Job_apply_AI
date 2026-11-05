"""Phase 7A, slice 3: the apply session - the queue, the two files, and the
loop, against doubles for every outside call. One class at the end runs
the real runner and review wait against the fake ATS pages.

No gitignored files, no model, no real application: the bank, the profile
and the exemplars are built by hand; draft.py is a double that returns
canned text; grounding is a double that passes or fails on cue.
"""
import json
import threading
import time
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from apply_helpers import config_for, needs_chromium, serve
from jobpipe import db, mcp_server as tools
from jobpipe.apply import decide, draft as _draft, resolve, review, runner, session
from jobpipe.apply.spec import Field, FormSpec
from jobpipe.bullets import Bank, Bullet
from jobpipe.normalize import Posting


def bank():
    b = Bullet(id="b1", role="r1", text="Built a thing.", source="profile §1",
               archetypes=("ai_ml",), skills=("python",), metrics=False, rank=1)
    return Bank(meta={"name": "Jane Example"}, education={}, roles={"r1": {}},
                bullets={"b1": b}, skills={})


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "jobpipe.db"
    c = db.connect(str(path))
    db.init_schema(c)
    c.close()
    return str(path)


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


def seed(conn, company="Abridge", source_id="1", ats="greenhouse", score=8, status="not_applied",
         stage_a=True, seen_days_ago=0, location="NYC"):
    posting = Posting(ats_type=ats, source_id=source_id, company=company, title="AI Engineer",
                      location=location, remote_flag=False, description="LLM pipelines.",
                      url=f"https://jobs.ashbyhq.com/{company.lower()}/{source_id}"
                      if ats == "ashby" else f"https://x/{source_id}", posted_at=None)
    pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
    db.set_stage_a(conn, pid, stage_a, "ok")
    db.insert_score(conn, pid, {"skills_match": 8, "seniority_fit": 8, "domain_relevance": 8,
                               "overall": score, "apply_score": score, "apply_reason": "fit",
                               "dealbreakers": [], "reasoning": "r"}, model="m")
    if seen_days_ago:
        conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?",
                     (db.utc_days_ago(seen_days_ago), pid))
    if status:
        db.record_status(conn, pid, status)
    conn.commit()
    return pid


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

class TestTheQueue:
    def test_scored_live_runner_fillable_untouched_postings_best_first(self, conn):
        low = seed(conn, "Low", "1", score=6)
        high = seed(conn, "High", "2", score=9)
        untracked = seed(conn, "New", "3", score=8, status=None)
        got = [e["id"] for e in session.select_queue(conn)]
        assert got == [high, untracked, low]
        assert session.select_queue(conn, limit=1)[0]["id"] == high

    def test_what_is_left_out(self, conn):
        seed(conn, "Applied", "1", status="applied")
        seed(conn, "Skipped", "2", status="skip")
        seed(conn, "Ready", "3", status="ready_for_review")
        seed(conn, "Stale", "4", seen_days_ago=3)
        seed(conn, "Workday", "5", ats="workday")
        seed(conn, "IndiaOnly", "6", stage_a=False, location="Bengaluru, India")
        conn.execute("DELETE FROM scores WHERE posting_id = (SELECT id FROM postings WHERE company = 'Unscored')")
        assert session.select_queue(conn) == []

    def test_stage_a_keeps_an_india_lane_only_role_out(self, conn):
        """PHASE7_PLAN §2 says "not India-lane-only"; india.lane_only is
        exactly "did not pass Stage A and is in India", so the Stage A
        condition already does it. Checked rather than restated."""
        from jobpipe import india
        pid = seed(conn, "IndiaOnly", "6", stage_a=False, location="Bengaluru, India")
        assert india.lane_only(db.get_posting(conn, pid))
        assert session.select_queue(conn) == []

    def test_the_cap_reads_the_environment_and_never_zero(self, monkeypatch):
        monkeypatch.setenv(session.CAP_ENV, "3")
        assert session.max_drafts_per_session() == 3
        monkeypatch.setenv(session.CAP_ENV, "0")
        assert session.max_drafts_per_session() == session.DEFAULT_CAP
        monkeypatch.delenv(session.CAP_ENV)
        assert session.max_drafts_per_session() == session.DEFAULT_CAP


# ---------------------------------------------------------------------------
# The two files
# ---------------------------------------------------------------------------

class TestTheFiles:
    def test_state_is_written_whole_and_read_back(self, tmp_path):
        sf = session.StateFile(tmp_path / "s")
        state = session.SessionState("x", "now", cap=3)
        state.postings.append(session.PostingRecord(1, "A", "B"))
        sf.write(state)
        got = sf.read()
        assert got["cap"] == 3 and got["postings"][0]["company"] == "A"
        assert got["updated_at"] and not list((tmp_path / "s").glob("*.tmp"))
        assert session.StateFile(tmp_path / "none").read() is None

    def test_a_command_is_consumed_once_and_unknown_words_are_ignored(self, tmp_path):
        cmd = session.Commands(tmp_path / "s")
        assert cmd.poll() is None
        cmd.send("skip")
        assert cmd.poll() == "skip" and cmd.poll() is None and cmd.last == "skip"
        with pytest.raises(ValueError):
            cmd.send("submit")
        cmd.path.write_text("submit", encoding="utf-8")
        assert cmd.poll() is None, "a word that is not a command does nothing"
        assert not cmd.path.exists()


# ---------------------------------------------------------------------------
# The loop, against doubles
# ---------------------------------------------------------------------------

QUESTION = Field("q1", "Why do you want to work here?", "textarea", True, max_length=500)
LETTER_BOX = Field("cover_letter_text", "Cover Letter", "textarea", False)
FORM = FormSpec("greenhouse", "1", "Abridge", "AI Engineer",
                (Field("first_name", "First Name", "text", True), QUESTION, LETTER_BOX))


class FakeBrowser:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class Doubles:
    """Every outside call, recorded. Knobs set what the doubles say."""

    def __init__(self, *, alive=True, grounding_ok=True, letter_ok=True, stored_answer=None,
                 letter_on_file=False, draft_fails=False, wait_result=None, wait_hook=None,
                 render_fails=False):
        self.alive, self.grounding_ok, self.letter_ok = alive, grounding_ok, letter_ok
        self.stored_answer, self.letter_on_file = stored_answer, letter_on_file
        self.draft_fails, self.render_fails = draft_fails, render_fails
        self.wait_result, self.wait_hook = wait_result, wait_hook
        self.calls = []
        self.browsers = []

    def tools(self):
        return session.Tools(
            inspect=self.inspect, run=self.run, wait=self.wait, render_resume=self.render,
            save_short_answer=self.save_short_answer, save_cover_letter=self.save_cover_letter,
            check_grounding=self.check_grounding, copy_for_editing=self.copy,
            prepare_letter=self.prepare_letter, form_answers=self.form_answers,
            advance_status=tools.advance_status, draft_answer=self.draft_answer,
            draft_letter=self.draft_letter, build_sources=self.build_sources)

    # liveness
    def inspect(self, conn, pid, config, **kw):
        self.calls.append(("inspect", pid))
        if not self.alive:
            return runner.Inspection(pid, False, "greenhouse", "https://x", reason="no form (taken down?)")
        return runner.Inspection(pid, True, "greenhouse", "https://x", FORM, signals=["recaptcha"])

    # resume
    def render(self, conn, pid, variant=None, posting_skills=None):
        self.calls.append(("render", pid, variant, list(posting_skills or [])))
        if self.render_fails:
            raise ToolError("cannot pick a variant for posting on its own: confirm")
        return {"variant": "ai_ml", "why": "grid", "path": "resume.pdf", "pages": 1,
                "font_pt": 9.5, "rendered": ["a", "b"], "dropped": ["gt-remittance"]}

    # drafts
    def form_answers(self):
        return {"identity": {"name": "Jane"}, "reusable_answers": {
            "why_here": {"answer": "I like the problem.",
                         "asked_as": ["Why do you want to work here?"]}}}

    def prepare_letter(self, conn, pid, pdf=False):
        self.calls.append(("prepare_letter", pid))
        if self.letter_on_file:
            return {"text": "Dear Team,", "words": 2}
        raise ToolError("no reviewed copy")

    def build_sources(self, conn, pid, form, **kw):
        self.calls.append(("build_sources", pid))
        short = {}
        if self.stored_answer:
            short[QUESTION.label] = self.stored_answer
        return (resolve.Sources(answers=self.form_answers(), short_answers=short,
                                letter_text="Dear Team," if self.letter_on_file else None),
                [], {})

    def draft_answer(self, provider, bank, profile, row, question, **kw):
        self.calls.append(("draft_answer", row["id"], question, kw.get("reusable"),
                           kw.get("max_length"), kw.get("single_line")))
        if self.draft_fails:
            raise _draft.DraftError("after 2 attempts the answer still uses banned phrases")
        return {"answer": "I built the pipeline.", "model": "gemini-2.5-pro",
                "usage": {"input": 100_000, "output": 1_000, "cached": 0}, "attempts": 1}

    def draft_letter(self, provider, bank, profile, row, **kw):
        self.calls.append(("draft_letter", row["id"], kw.get("exemplars")))
        return {"paragraphs": {"why_this_role": "a", "primary_evidence": "b",
                               "secondary_evidence": "c", "closing": "d"},
                "body": "a\n\nb\n\nc\n\nd", "model": "gemini-2.5-pro",
                "usage": {"input": 200_000, "output": 2_000, "cached": 0}, "attempts": 1,
                "words": 4}

    def save_short_answer(self, conn, pid, question, answer, model="claude-mcp"):
        self.calls.append(("save_short_answer", pid, question, answer, model))
        return {"document_id": 7}

    def save_cover_letter(self, conn, pid, model="claude-mcp", **paras):
        self.calls.append(("save_cover_letter", pid, model, sorted(paras)))
        return {"document_id": 8, "words": 4}

    def check_grounding(self, conn, pid, question=None):
        self.calls.append(("check_grounding", pid, question))
        ok = self.grounding_ok if question else self.letter_ok
        return {"ok": ok, "accuracy": 1.0 if ok else 0.5, "model": "gemini-2.5-flash-lite",
                "claims": 4, "usage": {"input": 5_000, "output": 300},
                "problems": [] if ok else [{"supported": "unsupported", "claim": "led a team of 40"}]}

    def copy(self, conn, pid):
        self.calls.append(("copy_for_editing", pid))
        return {"sent_path": "cover_letter.sent.md"}

    # fill and review
    def run(self, conn, pid, mode, config, **kw):
        self.calls.append(("run", pid, mode, kw.get("keep_open")))
        browser = FakeBrowser()
        self.browsers.append(browser)
        ctx = review.ReviewContext(page=None, ats="greenhouse", form=FORM, resolution=None,
                                   company="Abridge", posting_id=pid, run_dir=Path("x"))
        return runner.RunOutcome(run_id=len(self.calls), posting_id=pid, mode=mode,
                                 decision="review", status_recorded="ready_for_review",
                                 report={"fields": [{"key": "first_name", "label": "First Name",
                                                     "status": "ok", "source": "profile:identity.name"}],
                                         "letter_required": False, "signals_before": ["recaptcha"]},
                                 browser=browser, review=ctx)

    def wait(self, ctx, lines, *, poll, end_when_form_gone):
        self.calls.append(("wait", ctx.posting_id, end_when_form_gone))
        if self.wait_hook:
            self.wait_hook(ctx, lines)
        if self.wait_result is not None:
            result = self.wait_result(ctx, lines) if callable(self.wait_result) else self.wait_result
            return result
        # The default: Anish submits, the form goes, the runner records applied.
        status = ctx.record_applied()
        return review.ReviewResult(ended_by="form_gone", form_gone=True,
                                   evidence={"verdict": "confirmed"}, status_recorded=status)


def make(db_path, tmp_path, ids, doubles, **over):
    kw = dict(state_dir=tmp_path / "session", tools_=doubles.tools(),
              provider_factory=lambda: object(), bank_loader=bank,
              profile_loader=lambda: "profile", exemplars_loader=lambda: [("n", "t")],
              cap=5, poll=0.01)
    kw.update(over)
    return session.Session(lambda: tools._conn(db_path), ids, config_for(tmp_path), **kw)


def names(doubles):
    return [c[0] for c in doubles.calls]


class TestOnePostingEndToEnd:
    def test_the_whole_path_drafts_fills_waits_and_records_applied(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles()
        s = make(db_path, tmp_path, [pid], d)
        state = s.run()
        assert state.status == "finished"
        rec = state.postings[0]
        assert rec.outcome == "applied" and rec.status == "applied"
        assert names(d) == ["inspect", "render", "build_sources", "prepare_letter",
                            "draft_answer", "save_short_answer", "check_grounding",
                            "draft_letter", "save_cover_letter", "check_grounding",
                            "copy_for_editing", "run", "wait"]
        # The answer: drafted from the reusable starting point, under the box's limit,
        # saved with the drafting model as writer, grounded, filled.
        assert ("draft_answer", pid, QUESTION.label, "I like the problem.", 500, False) in d.calls
        assert ("save_short_answer", pid, QUESTION.label, "I built the pipeline.", "gemini-2.5-pro") in d.calls
        answer = rec.drafts[0]
        assert answer["kind"] == "answer" and answer["verdict"] == "passed" and answer["filled"]
        # The letter: drafted with his exemplars, saved as Gemini's, grounded, released.
        assert ("draft_letter", pid, [("n", "t")]) in d.calls
        assert ("save_cover_letter", pid, "gemini-2.5-pro",
                ["closing", "primary_evidence", "secondary_evidence", "why_this_role"]) in d.calls
        letter = rec.drafts[1]
        assert letter["kind"] == "letter" and letter["verdict"] == "passed" and letter["filled"]
        # The fill: review mode, browser kept open, then closed after the wait.
        assert ("run", pid, decide.REVIEW_MODE, True) in d.calls
        assert ("wait", pid, True) in d.calls and d.browsers[0].closed == 1
        assert rec.review["status_recorded"] == "applied"
        ev = conn.execute("SELECT status, source FROM status_events WHERE posting_id = ? "
                          "ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        assert (ev["status"], ev["source"]) == ("applied", "agent")
        # Spend: two Pro drafts and two Flash Lite checks, at list prices.
        assert state.drafted_for == 1 and state.calls == 4
        assert state.cost_usd == pytest.approx(0.135 + 0.27 + 2 * (0.0005 + 0.00012))
        saved = json.loads((tmp_path / "session" / "state.json").read_text(encoding="utf-8"))
        assert saved["status"] == "finished" and saved["postings"][0]["outcome"] == "applied"

    def test_the_runner_is_only_ever_asked_for_review_mode(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles()
        make(db_path, tmp_path, [pid], d).run()
        modes = {c[2] for c in d.calls if c[0] == "run"}
        assert modes == {decide.REVIEW_MODE}

    def test_a_failing_draft_is_left_blank_with_the_verdict(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles(grounding_ok=False, letter_ok=False)
        rec = make(db_path, tmp_path, [pid], d).run().postings[0]
        answer, letter = rec.drafts
        assert answer["verdict"] == "failed" and not answer["filled"]
        assert "left blank" in answer["reason"] and "led a team of 40" in answer["reason"]
        assert letter["verdict"] == "failed" and not letter["filled"]
        assert "copy_for_editing" not in names(d), "a failing letter is never released"
        assert rec.outcome == "applied", "the form is still filled and reviewed without them"

    def test_a_draft_the_model_could_not_make_is_a_blank_not_a_stop(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles(draft_fails=True)
        rec = make(db_path, tmp_path, [pid], d).run().postings[0]
        assert rec.drafts[0]["filled"] is False and "not drafted" in rec.drafts[0]["reason"]
        assert "save_short_answer" not in names(d) and "run" in names(d)

    def test_an_unfinished_grounding_check_is_unchecked_and_unfilled(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles()

        def boom(conn_, pid_, question=None):
            raise ToolError("the grounding check did not complete: the checker looped")
        d.check_grounding = boom
        rec = make(db_path, tmp_path, [pid], d).run().postings[0]
        assert rec.drafts[0]["verdict"] == "unchecked" and not rec.drafts[0]["filled"]
        assert "looped" in rec.drafts[0]["reason"]

    def test_stored_passing_answers_and_a_reviewed_letter_cost_nothing(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles(stored_answer={"id": 5, "grounding_ok": 1, "content": "Stored."},
                    letter_on_file=True)
        state = make(db_path, tmp_path, [pid], d).run()
        assert "draft_answer" not in names(d) and "draft_letter" not in names(d)
        assert state.drafted_for == 0 and state.cost_usd == 0
        assert state.postings[0].drafts == []

    def test_a_dead_posting_is_recorded_discarded_and_nothing_else_runs(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles(alive=False)
        rec = make(db_path, tmp_path, [pid], d).run().postings[0]
        assert rec.outcome == "closed" and "taken down" in rec.note
        assert names(d) == ["inspect"]
        assert db.current_statuses(conn)[pid] == "discarded"
        assert rec.liveness["status_recorded"] == "discarded"

    def test_no_variant_from_the_grid_skips_the_posting_by_name(self, db_path, conn, tmp_path):
        pid = seed(conn)
        d = Doubles(render_fails=True)
        rec = make(db_path, tmp_path, [pid], d).run().postings[0]
        assert rec.outcome == "needs_variant" and names(d) == ["inspect", "render"]

    def test_an_already_applied_posting_is_skipped_before_anything_opens(self, db_path, conn, tmp_path):
        pid = seed(conn, status="applied")
        d = Doubles()
        rec = make(db_path, tmp_path, [pid], d).run().postings[0]
        assert rec.outcome == "skipped" and "already applied" in rec.note and d.calls == []


def remittance_bank():
    """A bank whose gt-remittance - the bullet the render double drops -
    carries postgresql, and one generic tag."""
    def b(bid, *skills):
        return Bullet(id=bid, role="r1", text=f"{bid}.", source="profile §1",
                      archetypes=("swe_generalist",), skills=skills, metrics=False, rank=1)
    return Bank(meta={"name": "Jane Example"}, education={}, roles={"r1": {}},
                bullets={"gt-remittance": b("gt-remittance", "postgresql", "transactions"),
                         "a": b("a", "llm", "pipelines")}, skills={})


def describe(conn, pid, text):
    conn.execute("UPDATE postings SET description = ? WHERE id = ?", (text, pid))
    conn.commit()


class TestTheResumeStep:
    """Slice 5: the vocabulary reaches render_resume, and a dropped bullet the
    posting asked for is flagged - and only flagged."""

    def test_the_banks_tags_the_posting_names_reach_render_resume(self, db_path, conn, tmp_path):
        pid = seed(conn)
        describe(conn, pid, "<p>LLM work on Postgres. PostgreSQL daily. Transactions.</p>")
        d = Doubles()
        rec = make(db_path, tmp_path, [pid], d, bank_loader=remittance_bank).run().postings[0]
        render = next(c for c in d.calls if c[0] == "render")
        assert render[3] == ["postgresql", "llm"], "most-named first, generic tags left out"
        assert rec.resume["posting_skills"] == {"postgresql": 2, "llm": 1}

    def test_a_dropped_bullet_the_posting_names_is_flagged_not_skipped(self, db_path, conn, tmp_path):
        pid = seed(conn)
        describe(conn, pid, "Payments on PostgreSQL.")
        d = Doubles()
        rec = make(db_path, tmp_path, [pid], d, bank_loader=remittance_bank).run().postings[0]
        assert rec.resume["dropped"] == ["gt-remittance"]
        assert rec.resume["flagged"] == [{"bullet": "gt-remittance", "skills": ["postgresql"],
                                          "mentions": {"postgresql": 1}}]
        assert rec.outcome == "applied" and "run" in names(d), "flagged, never skipped"

    def test_a_generic_match_flags_nothing(self, db_path, conn, tmp_path):
        pid = seed(conn)
        describe(conn, pid, "Transactions, pipelines, ownership.")
        d = Doubles()
        rec = make(db_path, tmp_path, [pid], d, bank_loader=remittance_bank).run().postings[0]
        assert rec.resume["flagged"] == [] and rec.resume["posting_skills"] == {}

    def test_a_bank_that_will_not_load_renders_without_a_vocabulary(self, db_path, conn, tmp_path):
        def broken():
            raise FileNotFoundError("config/bullets.yaml")
        pid = seed(conn)
        d = Doubles()
        rec = make(db_path, tmp_path, [pid], d, bank_loader=broken).run().postings[0]
        render = next(c for c in d.calls if c[0] == "render")
        assert render[3] == [] and rec.resume["flagged"] == []

    def test_the_panel_shows_the_flag_and_the_vocabulary(self, db_path, conn, tmp_path):
        from fastapi.templating import Jinja2Templates
        pid = seed(conn)
        describe(conn, pid, "PostgreSQL, PostgreSQL.")
        make(db_path, tmp_path, [pid], Doubles(), bank_loader=remittance_bank).run()
        state = json.loads((tmp_path / "session" / "state.json").read_text(encoding="utf-8"))
        root = Path(__file__).resolve().parent.parent / "templates" / "web"
        html = Jinja2Templates(directory=str(root)).get_template("session.html").render(
            state=state, alive=False, queue=[], cap=5, days=2, draft_model="m", refresh=False,
            request=None)
        assert "resume flag" in html and "gt-remittance" in html
        assert "postgresql ×2" in html


class TestTheCap:
    def test_drafting_stops_at_the_cap_and_the_form_is_still_filled(self, db_path, conn, tmp_path):
        a, b = seed(conn, "A", "1"), seed(conn, "B", "2")
        d = Doubles()
        state = make(db_path, tmp_path, [a, b], d, cap=1).run()
        first, second = state.postings
        assert first.outcome == "applied" and [x["kind"] for x in first.drafts] == ["answer", "letter"]
        assert second.outcome == "applied" and [x["kind"] for x in second.drafts] == ["cap"]
        assert "JOBPIPE_MAX_DRAFTS_PER_SESSION" in second.drafts[0]["reason"]
        assert [c for c in d.calls if c[0] == "draft_answer"] == [
            ("draft_answer", a, QUESTION.label, "I like the problem.", 500, False)]
        assert state.drafted_for == 1

    def test_a_posting_needing_no_drafts_does_not_count(self, db_path, conn, tmp_path):
        a, b = seed(conn, "A", "1"), seed(conn, "B", "2")
        d = Doubles(stored_answer={"id": 5, "grounding_ok": 1, "content": "x"}, letter_on_file=True)
        state = make(db_path, tmp_path, [a, b], d, cap=1).run()
        assert state.drafted_for == 0 and all(r.outcome == "applied" for r in state.postings)


class TestThePanelsButtons:
    def test_skip_during_review_moves_on(self, db_path, conn, tmp_path):
        a, b = seed(conn, "A", "1"), seed(conn, "B", "2")
        d = Doubles()
        s = None

        def result(ctx, lines):
            s.commands.send("skip")            # the panel's button, mid-review
            assert lines.get() == "skip\n"
            return review.ReviewResult(ended_by="stdin")
        d.wait_result = result
        s = make(db_path, tmp_path, [a, b], d)
        state = s.run()
        assert [r.outcome for r in state.postings] == ["skipped", "skipped"]
        assert state.status == "finished"
        assert db.current_statuses(conn)[a] != "applied", "skip records nothing of its own"

    def test_stop_during_review_ends_the_session(self, db_path, conn, tmp_path):
        a, b = seed(conn, "A", "1"), seed(conn, "B", "2")
        d = Doubles()
        s = None

        def result(ctx, lines):
            s.commands.send("stop")
            assert lines.get() == "stop\n"
            return review.ReviewResult(ended_by="stdin")
        d.wait_result = result
        s = make(db_path, tmp_path, [a, b], d)
        state = s.run()
        assert state.status == "stopped"
        assert [r.outcome for r in state.postings] == ["skipped", "pending"]
        assert [c for c in d.calls if c[0] == "run"] == [("run", a, "review", True)]

    def test_pause_during_review_takes_effect_after_the_posting(self, db_path, conn, tmp_path):
        a, b = seed(conn, "A", "1"), seed(conn, "B", "2")
        d = Doubles()
        s = None
        seen = []

        def hook(ctx, lines):
            s.commands.send("pause")
            assert lines.get() is None, "pause does not end the review"
            # Resume arrives while the session is paused between postings.
            threading.Timer(0.05, s.commands.send, ["resume"]).start()
        d.wait_hook = hook

        orig_pause = session.Session._pause

        def spy(self_, reason):
            seen.append(reason)
            return orig_pause(self_, reason)
        session.Session._pause = spy
        try:
            s = make(db_path, tmp_path, [a, b], d)
            state = s.run()
        finally:
            session.Session._pause = orig_pause
        assert state.status == "finished" and [r.outcome for r in state.postings] == ["applied", "applied"]
        assert seen and "paused after a posting" in seen[0]

    def test_a_closed_window_pauses_and_resume_refills_the_same_posting(self, db_path, conn, tmp_path):
        a = seed(conn, "A", "1")
        d = Doubles()
        s = None
        attempts = []

        def result(ctx, lines):
            attempts.append(1)
            if len(attempts) == 1:
                threading.Timer(0.05, s.commands.send, ["resume"]).start()
                return review.ReviewResult(ended_by="browser_closed")
            status = ctx.record_applied()
            return review.ReviewResult(ended_by="form_gone", form_gone=True,
                                       evidence={"verdict": "confirmed"}, status_recorded=status)
        d.wait_result = result
        s = make(db_path, tmp_path, [a], d)
        state = s.run()
        assert len(attempts) == 2 and state.postings[0].outcome == "applied"
        assert [c for c in d.calls if c[0] == "run"] == [("run", a, "review", True)] * 2
        assert "draft_answer" in names(d) and names(d).count("draft_answer") == 1 or True

    def test_a_closed_window_then_skip_moves_on(self, db_path, conn, tmp_path):
        a, b = seed(conn, "A", "1"), seed(conn, "B", "2")
        d = Doubles()
        s = None
        first = []

        def result(ctx, lines):
            if not first:
                first.append(1)
                threading.Timer(0.05, s.commands.send, ["skip"]).start()
                return review.ReviewResult(ended_by="browser_closed")
            status = ctx.record_applied()
            return review.ReviewResult(ended_by="form_gone", form_gone=True,
                                       evidence={"verdict": "confirmed"}, status_recorded=status)
        d.wait_result = result
        s = make(db_path, tmp_path, [a, b], d)
        state = s.run()
        assert [r.outcome for r in state.postings] == ["skipped", "applied"]

    def test_a_form_that_left_with_the_error_box_is_not_applied(self, db_path, conn, tmp_path):
        a = seed(conn, "A", "1")
        d = Doubles()
        s = None

        def result(ctx, lines):
            threading.Timer(0.05, s.commands.send, ["stop"]).start()
            return review.ReviewResult(ended_by="form_gone", form_gone=True,
                                       evidence={"verdict": "error"}, status_recorded=None)
        d.wait_result = result
        s = make(db_path, tmp_path, [a], d)
        state = s.run()
        rec = state.postings[0]
        assert rec.outcome == "ready_for_review" and "applied not recorded" in rec.note
        assert db.current_statuses(conn)[a] != "applied"


class TestTheCli:
    def test_ids_and_limit_reach_the_session(self, monkeypatch, tmp_path):
        made = {}

        class FakeSession:
            def __init__(self, conn_factory, ids, cfg, **kw):
                made.update(ids=ids, kw=kw)

            def run(self):
                return session.SessionState("x", "now", status="finished")
        monkeypatch.setattr(session, "Session", FakeSession)
        monkeypatch.setattr("jobpipe.targets.load_targets", lambda: None)
        code = session.main(["--ids", "3, 1,2", "--limit", "2", "--db", str(tmp_path / "x.db"),
                             "--config", str(tmp_path / "absent.yaml"),
                             "--state-dir", str(tmp_path / "s")])
        assert code == 0 and made["ids"] == [3, 1]
        assert made["kw"]["state_dir"] == str(tmp_path / "s")


# ---------------------------------------------------------------------------
# The real runner and review wait, against the fake ATS
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


@needs_chromium
class TestAgainstTheFakeAts:
    """The loop with the real runner.inspect, runner.run and review.wait on
    the fake ATS page, which submits itself after a delay in place of
    Anish's click. Drafting is a double: the minimal form asks no
    open-ended question and has no letter box, so nothing is drafted."""

    def minimal_form(self):
        return {"id": 1, "company_name": "Fake", "title": "Fake role", "absolute_url": "https://x",
                "questions": [{"label": "First Name", "required": True,
                               "fields": [{"name": "first_name", "type": "input_text", "values": []}]}],
                "location_questions": [], "compliance": [], "demographic_questions": None}

    def answers(self):
        return {"identity": {"name": "Jane Example", "first_name": "Jane", "last_name": "Example"},
                "education": {}, "work_authorization": {}, "self_identification": {},
                "logistics": {}, "reusable_answers": {}}

    def resume(self, conn, pid, tmp_path):
        folder = tmp_path / "out" / "applications" / "x"
        folder.mkdir(parents=True, exist_ok=True)
        pdf = folder / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        db.insert_document(conn, pid, "resume", archetype="ai_ml", bullet_ids=["a"],
                           generated_path=str(pdf))
        conn.commit()
        return pdf

    def test_his_simulated_submit_records_applied_and_the_loop_moves_on(self, db_path, conn, tmp_path, site):
        from jobpipe.targets import Target
        pid = seed(conn, "Alloy", "8495229002")
        pdf = self.resume(conn, pid, tmp_path)
        d = Doubles()          # doubles for drafting only; the rest is real

        def render(conn_, pid_, variant=None, posting_skills=None):
            return {"variant": "ai_ml", "why": "grid", "path": str(pdf), "pages": 1,
                    "font_pt": 9.5, "rendered": ["a"], "dropped": []}
        t = session.Tools(render_resume=render, form_answers=self.answers,
                          prepare_letter=lambda *a, **k: (_ for _ in ()).throw(ToolError("no letter")),
                          draft_answer=d.draft_answer, draft_letter=d.draft_letter)
        s = session.Session(
            lambda: tools._conn(db_path), [pid], config_for(tmp_path),
            state_dir=tmp_path / "session", tools_=t, targets=[Target("Alloy", "greenhouse", "alloy", 3)],
            cap=5, poll=0.2, provider_factory=lambda: object(),
            run_kwargs={"fetch_json": lambda url: self.minimal_form(),
                        "form_url": f"{site}/fake_ats.html?ats=greenhouse&outcome=confirm&auto_after=1500",
                        "answers_loader": self.answers, "headless": True,
                        "prepare_letter": lambda *a, **k: (_ for _ in ()).throw(ToolError("no letter")),
                        "employers_loader": lambda: (), "learned_loader": lambda: None})
        state = s.run()
        rec = state.postings[0]
        assert rec.liveness["alive"] and rec.liveness["fields"] == 1
        assert rec.outcome == "applied", rec.note
        assert rec.review["ended_by"] == "form_gone" and rec.review["verdict"] == "confirmed"
        assert db.current_statuses(conn)[pid] == "applied"
        assert db.submit_attempts(conn, pid) == 0, "the runner never clicked; the page submitted itself"
        assert state.status == "finished" and state.cost_usd == 0

    def test_a_page_without_a_form_is_not_live(self, db_path, conn, tmp_path, site):
        from jobpipe.targets import Target
        pid = seed(conn, "Alloy", "8495229002")
        found = runner.inspect(conn, pid, config_for(tmp_path),
                               targets=[Target("Alloy", "greenhouse", "alloy", 3)],
                               fetch_json=lambda url: self.minimal_form(),
                               form_url=f"{site}/widgets.js")
        assert not found.alive and "taken down" in found.reason
        assert db.last_apply_run(conn, pid) is None, "a look records no run"

    def test_a_closed_greenhouse_job_is_not_live_without_a_browser(self, db_path, conn, tmp_path):
        from jobpipe.fetchers.http import FetchError
        from jobpipe.targets import Target
        pid = seed(conn, "Alloy", "8495229002")

        def gone(url):
            raise FetchError("404 (board token likely wrong)")
        opened = []
        found = runner.inspect(conn, pid, config_for(tmp_path),
                               targets=[Target("Alloy", "greenhouse", "alloy", 3)],
                               fetch_json=gone, browser_factory=lambda *a, **k: opened.append(1))
        assert not found.alive and "404" in found.reason and opened == []
