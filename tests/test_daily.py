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
