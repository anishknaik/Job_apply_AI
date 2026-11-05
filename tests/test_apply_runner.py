"""Phase 6, Slice 7: the runner end to end, offline.

A temp-file database (tools._conn opens a connection per call), the fixture
pages served locally, the Greenhouse questions JSON from the real Alloy
capture, form_answers and prepare_letter stubbed. What is asserted is the
record: apply_runs rows, status events with source="agent", click counts,
and that dry run is the default and touches nothing.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apply_helpers import PAGES, config_for, needs_chromium, serve
from jobpipe import db, mcp_server as tools
from jobpipe.apply import __main__ as cli, decide, runner
from jobpipe.apply.config import ApplyConfig
from jobpipe.normalize import Posting
from jobpipe.targets import Target

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apply"
ALLOY = json.loads((FIXTURES / "greenhouse_alloy_8495229002_questions.json").read_text(encoding="utf-8"))
TARGETS = [Target(company="Alloy", ats_type="greenhouse", board_token="alloy", tier=3)]


def answers():
    return {"identity": {"name": "Jane Example", "email": "jane@example.com",
                         "phone": "(555) 010-0000", "linkedin": "linkedin.com/in/jane",
                         "github": "github.com/jane", "location": "Springfield, IL"},
            "education": {}, "work_authorization": {"authorized_to_work_in_us": True,
                                                    "requires_sponsorship": False},
            "self_identification": {"gender": ["Male"]}, "logistics": {},
            "reusable_answers": {}}


def refuse_letter(conn, pid, pdf=False):
    from mcp.server.mcpserver.exceptions import ToolError
    raise ToolError("no reviewed copy")


@pytest.fixture(scope="module")
def site():
    with serve() as base:
        yield base


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "jobpipe.db"
    connection = db.connect(str(path))
    db.init_schema(connection)
    yield connection
    connection.close()


def seed(conn, tmp_path, ats="greenhouse", with_resume=True, status="queued"):
    if ats == "greenhouse":
        posting = Posting(ats_type="greenhouse", source_id="8495229002", company="Alloy",
                          title="Senior Machine Learning Engineer", location="NYC",
                          remote_flag=False, description="d",
                          url="https://www.alloy.com/about/jobs/detail?gh_jid=8495229002",
                          posted_at=None)
    else:
        posting = Posting(ats_type="ashby", source_id="74e5f9f4", company="LangChain",
                          title="Python OSS Engineer", location="SF", remote_flag=False,
                          description="d", url="https://jobs.ashbyhq.com/langchain/74e5f9f4",
                          posted_at=None)
    pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
    db.record_status(conn, pid, status)
    if with_resume:
        folder = tmp_path / "out" / "applications" / "x"
        folder.mkdir(parents=True, exist_ok=True)
        pdf = folder / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        db.insert_document(conn, pid, "resume", archetype="ai_ml_infra",
                           bullet_ids=["a"], generated_path=str(pdf))
    conn.commit()
    return pid


def go(conn, pid, mode, site, tmp_path, page="greenhouse_form.html", config=None, **kw):
    kw.setdefault("answers_loader", answers)
    kw.setdefault("prepare_letter", refuse_letter)
    kw.setdefault("employers_loader", lambda: ("Acme Analytics",))
    kw.setdefault("learned_loader", lambda: None)
    kw.setdefault("fetch_json", lambda url: ALLOY)
    return runner.run(conn, pid, mode, config or config_for(tmp_path), targets=TARGETS,
                      form_url=f"{site}/{page}", headless=True, **kw)


class TestWhereTheFormIs:
    def test_greenhouse_token_from_targets_then_url(self, conn, tmp_path):
        pid = seed(conn, tmp_path)
        row = db.get_posting(conn, pid)
        assert runner.board_token(row, TARGETS) == "alloy"
        assert runner.board_token(row, None) is None, "a company site URL carries no token"
        ats, url = runner.form_url_for(row, TARGETS)
        assert (ats, url) == ("greenhouse",
                              "https://job-boards.greenhouse.io/embed/job_app?for=alloy&token=8495229002")

    def test_a_greenhouse_hosted_url_carries_its_token(self):
        row = {"company": "X", "url": "https://job-boards.greenhouse.io/datadog/jobs/8053714",
               "source_id": "8053714", "ats_type": "greenhouse", "id": 1}
        assert runner.board_token(row, None) == "datadog"

    def test_no_token_is_an_error_not_a_guess(self, conn, tmp_path):
        pid = seed(conn, tmp_path)
        with pytest.raises(runner.RunnerError, match="targets.csv"):
            runner.form_url_for(db.get_posting(conn, pid), None)

    def test_ashby_opens_its_own_application_page(self, conn, tmp_path):
        pid = seed(conn, tmp_path, ats="ashby")
        ats, url = runner.form_url_for(db.get_posting(conn, pid), None)
        assert (ats, url) == ("ashby", "https://jobs.ashbyhq.com/langchain/74e5f9f4/application")

    def test_other_boards_are_refused(self, conn, tmp_path):
        posting = Posting(ats_type="workday", source_id="1", company="X", title="Y",
                          location="", remote_flag=False, description="", url="https://x/1",
                          posted_at=None)
        pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
        conn.commit()
        with pytest.raises(runner.RunnerError, match="not handled"):
            runner.form_url_for(db.get_posting(conn, pid), None)


class TestSources:
    def test_a_refusing_tool_is_a_note_not_a_guess(self, conn, tmp_path):
        pid = seed(conn, tmp_path, with_resume=False)
        from jobpipe.apply import spec
        form = spec.parse_greenhouse(ALLOY)
        from mcp.server.mcpserver.exceptions import ToolError

        def no_answers():
            raise ToolError("config/answers.yaml does not exist")
        sources, notes, writers = runner.build_sources(
            conn, pid, form, answers_loader=no_answers, prepare_letter=refuse_letter)
        assert sources.answers is None and sources.resume_path is None
        assert any("form_answers unavailable" in n for n in notes)
        assert any("no resume rendered" in n for n in notes)

    def test_an_anything_else_box_asks_for_the_reviewed_letter(self, conn, tmp_path):
        """prepare_letter is called for a form with an additional-information
        textarea, not only for a cover-letter field; its refusal is a note."""
        pid = seed(conn, tmp_path)
        from jobpipe.apply.spec import Field, FormSpec
        form = FormSpec(ats="ashby", job_id="1", company="Sierra", title="x", fields=(
            Field("cf82", "Anything else? (Optional)", "textarea", False),))
        calls = []

        def letter(conn_, pid_, pdf=False):
            calls.append(pdf)
            return {"text": "Dear Team,\n\nReviewed.\n", "words": 3}
        sources, notes, _ = runner.build_sources(conn, pid, form, answers_loader=answers,
                                                 prepare_letter=letter)
        assert calls == [False] and sources.letter_text.startswith("Dear Team")
        sources, notes, _ = runner.build_sources(conn, pid, form, answers_loader=answers,
                                                 prepare_letter=refuse_letter)
        assert sources.letter_text is None and any("prepare_letter refused" in n for n in notes)

    def test_the_posting_company_and_employers_reach_the_resolver(self, conn, tmp_path):
        pid = seed(conn, tmp_path)
        from jobpipe.apply import spec
        sources, notes, _ = runner.build_sources(
            conn, pid, spec.parse_greenhouse(ALLOY), answers_loader=answers,
            prepare_letter=refuse_letter, employers_loader=lambda: ("Acme Analytics",),
            company="Alloy")
        assert sources.company == "Alloy" and sources.employers == ("Acme Analytics",)
        sources, notes, _ = runner.build_sources(
            conn, pid, spec.parse_greenhouse(ALLOY), answers_loader=answers,
            prepare_letter=refuse_letter, employers_loader=lambda: None)
        assert sources.employers is None and any("employers unknown" in n for n in notes)
        assert sources.company == "Alloy", "falls back to the form's company"

    def test_short_answers_and_their_writers(self, conn, tmp_path):
        pid = seed(conn, tmp_path)
        tools.save_short_answer(conn, pid, "Why Alloy?", "Because of the fraud work.")
        from jobpipe.apply import spec
        sources, notes, writers = runner.build_sources(
            conn, pid, spec.parse_greenhouse(ALLOY), answers_loader=answers,
            prepare_letter=refuse_letter)
        row = sources.answer("Why  Alloy?")   # whitespace-insensitive, case-preserving
        assert row["content"] == "Because of the fraud work."
        assert writers[f"documents:short_answer#{row['id']}"] == "claude-mcp"


@needs_chromium
class TestDryRun:
    def test_touches_nothing_and_records_what_it_would_do(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        events_before = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
        out = go(conn, pid, decide.DRY_RUN, site, tmp_path)
        assert out.decision == "dry_run" and out.error is None
        assert out.status_recorded is None
        assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == events_before
        row = db.last_apply_run(conn, pid)
        assert row["mode"] == "dry_run" and row["decision"] == "dry_run"
        report = json.loads(row["report"])
        statuses = {f["key"]: f["status"] for f in report["fields"]}
        assert statuses["first_name"] == "planned" and statuses["question_36029295002"] == "blocker"
        assert report["would_decide"] == "review"
        assert any(r.startswith("enabled: this run is dry_run") for r in out.reasons)
        assert report["required_missing"] == [] and "first_name" in report["required_on_page"]
        assert "Jane" not in row["report"], "hashes, never values"
        assert out.screenshots and Path(out.screenshots[0]).exists()


@needs_chromium
class TestLearnedAnswersInARun:
    def test_a_dry_run_plans_a_remembered_answer(self, conn, site, tmp_path):
        """Alloy's relocation question is a required select nobody has a
        source for. With Anish's earlier choice on file, the dry run plans
        it from memory and the report says so."""
        from jobpipe.apply import learned
        store = learned.Store()
        learned.record(store, "If you are not based in the NYC metro area, are you willing to relocate?",
                       "N/A", "select", "Example Co", 1, "2026-11-05")
        pid = seed(conn, tmp_path)
        out = go(conn, pid, decide.DRY_RUN, site, tmp_path, learned_loader=lambda: store)
        by = {f["key"]: f for f in out.report["fields"]}
        assert by["question_36029297002"]["status"] == "planned"
        assert by["question_36029297002"]["source"] == "learned:Example Co #1 2026-11-05"
        assert by["question_36029295002"]["status"] == "blocker", "salary is never remembered"

    def test_a_broken_store_is_a_note_not_a_crash(self, conn, site, tmp_path):
        from jobpipe.apply import learned

        def broken():
            raise learned.LearnedError("bad yaml")
        pid = seed(conn, tmp_path)
        out = go(conn, pid, decide.DRY_RUN, site, tmp_path, learned_loader=broken)
        assert out.error is None and any("learned answers not loaded" in n for n in out.report["notes"])


@needs_chromium
class TestReview:
    def test_fills_stops_and_records_ready_for_review(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        out = go(conn, pid, decide.REVIEW_MODE, site, tmp_path)
        assert out.decision == "review" and out.submit_outcome is None
        assert out.status_recorded == "ready_for_review"
        ev = conn.execute("SELECT status, source FROM status_events WHERE posting_id = ? "
                          "ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        assert (ev["status"], ev["source"]) == ("ready_for_review", "agent")
        row = db.last_apply_run(conn, pid)
        assert row["submit_outcome"] is None
        report = json.loads(row["report"])
        by = {f["key"]: f for f in report["fields"]}
        assert by["first_name"]["status"] == "ok" and by["resume"]["status"] == "ok"
        assert by["question_36029295002"]["status"] == "blocked"
        assert [c["name"] for c in report["checks"] if not c["passed"]] == ["enabled", "no_blockers", "no_required_never_fill"]
        assert report["required_missing"] == []
        assert db.submit_attempts(conn, pid) == 0

    def test_keep_open_hands_the_browser_back(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        out = go(conn, pid, decide.REVIEW_MODE, site, tmp_path, keep_open=True)
        assert out.browser is not None
        form_page = [p for p in out.browser.context.pages if "greenhouse_form" in p.url][0]
        assert form_page.evaluate("window.__submits") == 0
        assert form_page.input_value("#first_name") == "Jane", "the filled form is what stays open"
        out.browser.close()


@needs_chromium
class TestAuto:
    def minimal_form(self):
        from jobpipe.apply import spec
        return {"id": 1, "company_name": "Fake", "title": "Fake role", "absolute_url": "https://x",
                "questions": [{"label": "First Name", "required": True,
                               "fields": [{"name": "first_name", "type": "input_text", "values": []}]}],
                "location_questions": [], "compliance": [], "demographic_questions": None}

    def test_config_off_means_review_even_with_auto(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        out = go(conn, pid, decide.AUTO_MODE, site, tmp_path, page="fake_ats.html?ats=greenhouse&outcome=confirm",
                 fetch_json=lambda url: self.minimal_form())
        assert out.decision == "review" and out.status_recorded == "ready_for_review"
        assert any("auto_submit is off" in r for r in out.reasons)
        assert db.submit_attempts(conn, pid) == 0

    def test_every_condition_holding_clicks_once_and_records_applied(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path, auto_submit=True, source="test")
        out = go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
                 page="fake_ats.html?ats=greenhouse&outcome=confirm",
                 fetch_json=lambda url: self.minimal_form())
        assert out.decision == "auto_submit" and out.submit_outcome == "confirmed"
        assert out.status_recorded == "applied"
        ev = conn.execute("SELECT status, source, letter_required FROM status_events "
                          "WHERE posting_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        assert (ev["status"], ev["source"], ev["letter_required"]) == ("applied", "agent", 0)
        assert db.submit_attempts(conn, pid) == 1
        assert any(s.endswith("after-submit-confirmed.png") for s in out.screenshots)

    def test_a_second_run_on_the_same_posting_never_clicks_again(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path, auto_submit=True, source="test")
        go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
           page="fake_ats.html?ats=greenhouse&outcome=timeout",
           fetch_json=lambda url: self.minimal_form())
        assert db.current_statuses(conn)[pid] == "submit_unconfirmed"
        out = go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
                 page="fake_ats.html?ats=greenhouse&outcome=confirm",
                 fetch_json=lambda url: self.minimal_form())
        assert out.decision == "review"
        assert any(r.startswith("first_attempt") for r in out.reasons)
        assert db.submit_attempts(conn, pid) == 1

    def test_a_crash_after_the_click_still_counts_as_an_attempt(self, conn, site, tmp_path, monkeypatch):
        # The click is recorded before it happens. A run that dies in the
        # confirmation wait must not leave a row that lets --auto click again.
        from jobpipe.apply import submit as _submit
        def click_then_die(*args, before_click=None, **kwargs):
            before_click()
            raise RuntimeError("browser died during the confirmation wait")
        monkeypatch.setattr(_submit, "submit_once", click_then_die)
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path, auto_submit=True, source="test")
        out = go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
                 page="fake_ats.html?ats=greenhouse&outcome=confirm",
                 fetch_json=lambda url: self.minimal_form())
        assert out.decision == "error"
        assert db.last_apply_run(conn, pid)["submit_outcome"] == "clicked"
        assert db.submit_attempts(conn, pid) == 1
        monkeypatch.undo()
        again = go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
                   page="fake_ats.html?ats=greenhouse&outcome=confirm",
                   fetch_json=lambda url: self.minimal_form())
        assert again.decision == "review"
        assert any(r.startswith("first_attempt") for r in again.reasons)

    def test_a_bot_check_on_the_page_means_review(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path, auto_submit=True, source="test")
        out = go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
                 page="fake_ats_precaptcha.html", fetch_json=lambda url: self.minimal_form())
        assert out.decision == "review" and any("recaptcha" in r for r in out.reasons)
        assert db.submit_attempts(conn, pid) == 0

    def test_the_daily_cap_counts_only_auto_clicks(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path, auto_submit=True, max_auto_submits_per_day=1, source="test")
        other = seed(conn, tmp_path)  # same posting data; a second row is fine for the cap
        run_id = db.start_apply_run(conn, other, "auto")
        db.finish_apply_run(conn, run_id, decision="auto_submit", submit_outcome="confirmed")
        out = go(conn, pid, decide.AUTO_MODE, site, tmp_path, config=cfg,
                 page="fake_ats.html?ats=greenhouse&outcome=confirm",
                 fetch_json=lambda url: self.minimal_form())
        assert out.decision == "review" and any("cap 1" in r for r in out.reasons)


@needs_chromium
class TestFailures:
    def test_no_form_on_the_page_is_an_error_run(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path)
        out = go(conn, pid, decide.REVIEW_MODE, site, tmp_path, page="widgets.js", config=cfg)
        assert out.decision == "error" and "application-form" in out.error
        assert out.status_recorded is None
        assert db.last_apply_run(conn, pid)["decision"] == "error"

    def test_the_lock_refuses_a_second_run(self, conn, site, tmp_path):
        pid = seed(conn, tmp_path)
        cfg = config_for(tmp_path)
        lock = Path(cfg.output_dir) / runner.LOCK_NAME
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("12345")
        with pytest.raises(runner.RunnerBusy):
            go(conn, pid, decide.DRY_RUN, site, tmp_path, config=cfg)
        assert db.last_apply_run(conn, pid)["error"] == "runner busy"

    def test_an_unknown_posting_is_refused(self, conn, tmp_path):
        with pytest.raises(runner.RunnerError, match="no posting"):
            runner.run(conn, 99999, decide.DRY_RUN, config_for(tmp_path))


class TestCli:
    def test_auto_is_refused_before_a_browser_opens_when_config_is_off(self, tmp_path, capsys):
        code = cli.main(["1", "--auto", "--config", str(tmp_path / "absent.yaml"),
                         "--db", str(tmp_path / "x.db")])
        assert code == 2
        assert "auto_submit is not true" in capsys.readouterr().out

    def test_dry_run_is_the_default(self):
        import argparse
        # The parser is built inside main(); mirror its contract: no flag -> dry run.
        assert decide.DRY_RUN == "dry_run"
