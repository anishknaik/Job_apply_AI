"""The unattended run. No network, no spend — every stage is stubbed.

The guarantee under test is the one that matters at 07:00 with nobody
watching: a stage that fails must not cost the run the stages after it. The
spec states this for a single company failing a fetch; it applies with more
force to a whole stage, because a Sheets outage would otherwise mean the day's
new postings are fetched, scored, paid for, and then never surfaced.
"""
import types

import pytest

from jobpipe import cli


def args(**kw):
    base = dict(db=":memory:", yes=False, limit=20, min_score=4, no_sort=True,
                company=None, refilter=False, provider=None, model=None,
                escalation_model=None, rescore=False, targets=None,
                filters=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def stages(monkeypatch):
    """Record which stages ran, and let a test make any of them misbehave."""
    ran = []

    def ok(name):
        def run(_a):
            ran.append(name)
            return 0
        return run

    for name in ("fetch", "filter", "score", "sync"):
        monkeypatch.setattr(cli, f"cmd_{name}", ok(name))
    return ran


class TestStageIsolation:
    def test_a_raising_stage_does_not_stop_the_rest(self, stages, monkeypatch):
        def boom(_a):
            raise RuntimeError("greenhouse 503")

        monkeypatch.setattr(cli, "cmd_fetch", boom)
        assert cli.cmd_daily(args(yes=True)) == 1
        assert stages == ["filter", "score", "sync"]

    def test_a_failing_sync_still_reports_but_runs_everything(self, stages,
                                                              monkeypatch):
        monkeypatch.setattr(cli, "cmd_sync", lambda _a: 1)
        assert cli.cmd_daily(args(yes=True)) == 1
        assert stages == ["fetch", "filter", "score"]

    def test_a_clean_run_exits_zero(self, stages):
        assert cli.cmd_daily(args(yes=True)) == 0
        assert stages == ["fetch", "filter", "score", "sync"]


class TestSpendGate:
    """Scheduling the job must not be the same act as authorising spend."""

    def test_score_is_skipped_without_yes(self, stages):
        assert cli.cmd_daily(args(yes=False)) == 0
        assert "score" not in stages
        assert stages == ["fetch", "filter", "sync"]

    def test_the_free_stages_still_run_without_yes(self, stages):
        cli.cmd_daily(args(yes=False))
        assert "fetch" in stages and "sync" in stages


class TestRunIsRecorded:
    """`runs` was empty and `cli stats`' recent-runs section permanently blank.

    run_daily.py calls start_run/finish_run but daily.ps1 invokes `cli daily`,
    so run_daily.py is orphaned and nothing ever wrote a row. With scoring
    moving off the schedule the cadence stops being uniform, and "when did the
    last fetch actually run" becomes a question with no other answer.
    """

    @pytest.fixture
    def dbfile(self, tmp_path):
        return str(tmp_path / "runs.db")

    def test_a_clean_run_writes_a_finished_row(self, stages, dbfile):
        from jobpipe import db
        assert cli.cmd_daily(args(db=dbfile)) == 0
        conn = db.connect(dbfile)
        rows = conn.execute("SELECT * FROM runs").fetchall()
        assert len(rows) == 1
        assert rows[0]["started_at"] and rows[0]["finished_at"]
        assert not rows[0]["errors"]

    def test_a_degraded_run_records_which_stage_failed(self, stages, dbfile,
                                                       monkeypatch):
        def boom(_a):
            raise RuntimeError("greenhouse 503")
        monkeypatch.setattr(cli, "cmd_fetch", boom)

        assert cli.cmd_daily(args(db=dbfile)) == 1
        from jobpipe import db
        row = db.connect(dbfile).execute("SELECT * FROM runs").fetchone()
        # The row still closes: a degraded run is a run, and a run with no
        # finished_at is indistinguishable from one that is still going.
        assert row["finished_at"]
        assert "fetch" in row["errors"]

    def test_each_invocation_is_its_own_row(self, stages, dbfile):
        for _ in range(3):
            cli.cmd_daily(args(db=dbfile))
        from jobpipe import db
        assert db.connect(dbfile).execute(
            "SELECT COUNT(*) FROM runs").fetchone()[0] == 3

    def test_stage_a_pass_counts_this_run_not_the_whole_survivor_pool(
            self, stages, dbfile):
        """Caught on the first real run: it recorded 554 against a log line
        reading "passed 3".

        The column means "how many passed Stage A in THIS run", which is what
        run_daily.py recorded and what the log prints. Keying on last_seen_at
        counted every live survivor the fetch happened to see again — a number
        that would have looked like a filter regression forever after, and one
        nobody would have questioned because it is plausible.
        """
        from jobpipe import db
        conn = db.connect(dbfile)
        db.init_schema(conn)
        from jobpipe.normalize import Posting

        # An old survivor, seen again today but first seen long ago.
        old = Posting(ats_type="ashby", source_id="old", company="X",
                      title="ML Engineer", location="NYC", remote_flag=False,
                      description="", url="u1", posted_at=None)
        pid, _ = db.upsert_posting(conn, old, old.content_hash,
                                   seen_at=db.utc_days_ago(30))
        db.set_stage_a(conn, pid, True, "passed")
        conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?",
                     (db.utcnow(), pid))
        conn.commit()
        conn.close()

        assert cli.cmd_daily(args(db=dbfile)) == 0
        conn = db.connect(dbfile)
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC "
                           "LIMIT 1").fetchone()
        assert row["stage_a_pass"] == 0, (
            "an old survivor seen again is not a posting that passed today")
