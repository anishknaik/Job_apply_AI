"""Phase 6, Slice 0: the guardrail's plumbing.

Not the runner - the things every later slice assumes: auto-submit is off
unless a gitignored file says otherwise, the two new states sit where
`is_regression` needs them, `apply_runs` is append-only and remembers a submit
click, and a status set by the runner records as source="agent".
"""
from pathlib import Path

import pytest
import yaml

from jobpipe import db, mcp_server, states
from jobpipe.apply import config as apply_config
from jobpipe.normalize import Posting

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def seed(conn, source_id="1"):
    posting = Posting(ats_type="greenhouse", source_id=source_id,
                      company="Alloy", title="Senior ML Engineer",
                      location="NYC", remote_flag=False, description="d",
                      url=f"https://x/{source_id}", posted_at=None)
    pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
    db.record_status(conn, pid, "not_applied")
    conn.commit()
    return pid


class TestAutoSubmitIsOffUnlessAFileSaysOtherwise:
    def test_no_file_means_defaults_and_off(self, tmp_path):
        cfg = apply_config.load(tmp_path / "absent.yaml")
        assert cfg.auto_submit is False
        assert cfg.max_auto_submits_per_day == 5
        assert cfg.min_minutes_between == 10
        assert cfg.browser.headed is True
        assert cfg.source == "defaults"

    def test_the_example_file_parses_and_is_off(self):
        """The example is what gets copied, so it must be a safe starting
        point: every key present, auto_submit false."""
        cfg = apply_config.load(REPO / "config" / "apply.example.yaml")
        assert cfg.auto_submit is False
        raw = yaml.safe_load((REPO / "config" / "apply.example.yaml").read_text())
        assert raw["auto_submit"] is False
        assert set(raw) >= {"auto_submit", "max_auto_submits_per_day",
                            "min_minutes_between", "browser", "output_dir"}

    def test_the_real_file_is_gitignored(self):
        ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
        assert "config/apply.yaml" in ignore
        assert ".jobpipe-browser/" in ignore
        assert "output/apply/" in ignore
        assert not (REPO / "config" / "apply.yaml").exists(), \
            "a real apply.yaml must never be in the tree"

    def test_a_string_true_does_not_turn_it_on(self, tmp_path):
        """YAML quoting: "true" is a string. A string must not read as on."""
        path = tmp_path / "apply.yaml"
        path.write_text('auto_submit: "true"\n')
        with pytest.raises(apply_config.ApplyConfigError, match="auto_submit"):
            apply_config.load(path)

    def test_a_real_true_turns_it_on(self, tmp_path):
        path = tmp_path / "apply.yaml"
        path.write_text("auto_submit: true\nmax_auto_submits_per_day: 2\n")
        cfg = apply_config.load(path)
        assert cfg.auto_submit is True
        assert cfg.max_auto_submits_per_day == 2
        assert cfg.source == str(path)

    def test_a_negative_cap_is_refused(self, tmp_path):
        path = tmp_path / "apply.yaml"
        path.write_text("max_auto_submits_per_day: -1\n")
        with pytest.raises(apply_config.ApplyConfigError):
            apply_config.load(path)


class TestTheTwoNewStates:
    def test_they_exist_and_sit_before_applied(self):
        ids = [s.id for s in states.load_states()]
        assert ids.index("applying") < ids.index("ready_for_review") \
            < ids.index("submit_unconfirmed") < ids.index("applied")

    def test_neither_is_terminal(self):
        assert not states.is_terminal("ready_for_review")
        assert not states.is_terminal("submit_unconfirmed")

    def test_moving_on_to_applied_is_not_a_regression(self):
        assert not states.is_regression("ready_for_review", "applied")
        assert not states.is_regression("submit_unconfirmed", "applied")
        assert not states.is_regression("applying", "ready_for_review")

    def test_a_sheet_cell_resolves_them(self):
        assert states.normalise("Ready for review") == "ready_for_review"
        assert states.normalise("filled") == "ready_for_review"
        assert states.normalise("Submit unconfirmed") == "submit_unconfirmed"


class TestApplyRuns:
    def test_a_run_opens_before_and_closes_after(self, conn):
        pid = seed(conn)
        run = db.start_apply_run(conn, pid, "review", ats="greenhouse")
        row = db.last_apply_run(conn, pid)
        assert row["id"] == run and row["finished_at"] is None
        assert row["mode"] == "review" and row["submit_outcome"] is None
        db.finish_apply_run(conn, run, decision="review",
                            reasons=["bot check: recaptcha"],
                            report={"first_name": {"source": "profile"}},
                            screenshots=["output/apply/1.png"])
        row = db.last_apply_run(conn, pid)
        assert row["finished_at"] and row["decision"] == "review"
        assert "recaptcha" in row["reasons"]
        assert row["submit_outcome"] is None

    def test_a_second_attempt_is_a_second_row(self, conn):
        pid = seed(conn)
        first = db.start_apply_run(conn, pid, "dry_run")
        db.finish_apply_run(conn, first, decision="review")
        second = db.start_apply_run(conn, pid, "review")
        assert second != first
        assert [r["id"] for r in db.apply_runs_for(conn, pid)] == [first, second]

    def test_submit_attempts_counts_every_click_whatever_happened(self, conn):
        """A click that produced no confirmation is still a click. The count
        is what refuses a second one."""
        pid = seed(conn)
        assert db.submit_attempts(conn, pid) == 0
        run = db.start_apply_run(conn, pid, "auto")
        db.finish_apply_run(conn, run, decision="auto_submit",
                            submit_outcome="unconfirmed")
        assert db.submit_attempts(conn, pid) == 1
        run = db.start_apply_run(conn, pid, "review")
        db.finish_apply_run(conn, run, decision="review")
        assert db.submit_attempts(conn, pid) == 1, "a review run is not a click"

    def test_auto_submits_since_reads_only_auto_clicks(self, conn):
        a, b = seed(conn, "a"), seed(conn, "b")
        run = db.start_apply_run(conn, a, "auto")
        db.finish_apply_run(conn, run, decision="auto_submit",
                            submit_outcome="confirmed")
        run = db.start_apply_run(conn, b, "review")
        db.finish_apply_run(conn, run, decision="review")
        rows = db.auto_submits_since(conn, "2000-01-01T00:00:00+00:00")
        assert [r["posting_id"] for r in rows] == [a]
        assert db.auto_submits_since(conn, "2999-01-01T00:00:00+00:00") == []

    def test_the_schema_migrates_an_existing_database(self, tmp_path):
        """init_schema on a database that predates the table adds it."""
        path = tmp_path / "old.db"
        connection = db.connect(path)
        connection.executescript(
            db.SCHEMA.split("CREATE TABLE IF NOT EXISTS apply_runs")[0])
        connection.commit()
        db.init_schema(connection)
        assert connection.execute(
            "SELECT COUNT(*) FROM apply_runs").fetchone()[0] == 0
        connection.close()


class TestAdvanceStatusFromTheRunner:
    def test_source_agent_is_recorded(self, conn):
        pid = seed(conn)
        out = mcp_server.advance_status(conn, pid, "ready_for_review",
                                        source="agent")
        assert out["recorded"] is True
        row = conn.execute("SELECT source, status FROM status_events "
                           "WHERE posting_id = ? ORDER BY id DESC LIMIT 1",
                           (pid,)).fetchone()
        assert (row["source"], row["status"]) == ("agent", "ready_for_review")

    def test_applied_still_needs_the_letter_flag_reminder(self, conn):
        pid = seed(conn)
        out = mcp_server.advance_status(conn, pid, "applied", source="agent")
        assert "letter-required" in out["reminder"]
