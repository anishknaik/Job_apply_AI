"""Calibration sampling (addendum §5).

The metric that matters is spread, not MAE — "a 0.8 MAE across a 3-point range
is a useless ranking". These tests guard the sampler that makes spread
possible in the first place.
"""
import csv
from pathlib import Path

import pytest

from jobpipe import calibrate as _calibrate_mod


class TestEffectiveCutoff:
    """The recall report must grade the rule the sheet actually uses.

    The sheet takes the top 20 by rank; a hardcoded threshold reports misses
    for roles that would have been surfaced anyway.
    """

    def _conn_with(self, scores):
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE scores (apply_score INTEGER)")
        c.executemany("INSERT INTO scores VALUES (?)", [(s,) for s in scores])
        return c

    def test_returns_the_nth_ranked_score(self):
        conn = self._conn_with([10, 9, 9, 8, 7, 6, 5, 4])
        assert _calibrate_mod.effective_cutoff(conn, limit=4) == 8
        assert _calibrate_mod.effective_cutoff(conn, limit=1) == 10

    def test_falls_back_when_the_corpus_is_smaller_than_n(self):
        conn = self._conn_with([9, 8])
        assert _calibrate_mod.effective_cutoff(conn, limit=20,
                                               fallback=6) == 6

    def test_falls_back_on_an_empty_scores_table(self):
        assert _calibrate_mod.effective_cutoff(
            self._conn_with([]), fallback=6) == 6

from jobpipe import calibrate, db
from jobpipe.normalize import Posting


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def seed(conn, titles):
    for i, title in enumerate(titles):
        posting = Posting(ats_type="ashby", source_id=str(i), company=f"Co{i}",
                          title=title, location="New York", remote_flag=False,
                          description="", url=f"https://x/{i}", posted_at=None)
        pid, _ = db.upsert_posting(conn, posting, posting.content_hash)
        db.set_stage_a(conn, pid, True, "passed")


# Mirrors the real pool: mostly "other", very little early-career.
POOL = (["Software Engineer, New Grad"] * 3
        + ["Machine Learning Engineer"] * 34
        + ["Software Engineer, Platform"] * 43
        + ["Silicon Engineer"] * 67)


class TestClassification:
    def test_assigns_the_expected_lanes(self):
        cases = {
            "Software Engineer, New Grad (2027)": "early-career",
            "Machine Learning Engineer": "strong-lane",
            "Software Engineer, Platform": "generic-swe",
            "Silicon Engineer": "other",
            "Technical Documentation Engineer": "other",
        }
        for title, lane in cases.items():
            assert calibrate._classify(title) == lane, title

    def test_early_career_wins_over_other_lanes(self):
        """A new-grad ML role is an early-career posting first."""
        assert calibrate._classify(
            "Machine Learning Engineer, New Grad") == "early-career"


class TestStratifiedSampling:
    def test_covers_every_lane_present(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)

        lanes = [r["lane"] for r in calibrate.read_rows(path)]
        assert set(lanes) == {"early-career", "strong-lane", "generic-swe",
                              "other"}

    def test_does_not_drown_in_the_largest_stratum(self, conn, tmp_path):
        """"other" is 46% of the real pool and is where the 1-3 scores live."""
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)

        lanes = [r["lane"] for r in calibrate.read_rows(path)]
        assert lanes.count("other") <= 8, \
            "a bottom-heavy set cannot calibrate the 6-vs-8 boundary"

    def test_takes_every_early_career_posting_when_they_are_scarce(self, conn,
                                                                   tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)

        lanes = [r["lane"] for r in calibrate.read_rows(path)]
        assert lanes.count("early-career") == 3, \
            "only 3 exist in the pool; all should be sampled"

    def test_returns_the_requested_size(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        assert calibrate.write_template(conn, path, 20) == 20
        rows = list(calibrate.read_rows(path))
        assert len(rows) == 20

    def test_a_thin_stratum_does_not_shrink_the_sample(self, conn, tmp_path):
        """The early-career quota is 4 but only 3 exist — the rest must be
        made up elsewhere rather than yielding a 19-row sheet."""
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        n = calibrate.write_template(conn, path, 20)
        assert n == 20

    def test_never_repeats_a_posting(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)
        ids = [r["posting_id"] for r in
               calibrate.read_rows(path)]
        assert len(ids) == len(set(ids))

    def test_uniform_mode_still_available(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        assert calibrate.write_template(conn, path, 20,
                                        stratified=False) == 20

    def test_refuses_when_there_are_no_survivors(self, conn, tmp_path):
        with pytest.raises(RuntimeError, match="no Stage A survivors"):
            calibrate.write_template(conn, tmp_path / "c.csv", 20)

    def test_sheet_is_blank_and_ready_to_score(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)
        rows = list(calibrate.read_rows(path))
        assert all(r["my_fit"] == "" and r["my_apply"] == "" for r in rows)
        assert all(r["url"].startswith("https://") for r in rows)


class TestHoldoutExclusion:
    """A holdout that overlaps the tuning set measures fit, not accuracy."""

    def test_excluded_postings_never_appear(self, conn, tmp_path):
        seed(conn, POOL)
        first = tmp_path / "a.csv"
        calibrate.write_template(conn, first, 20)
        already = {int(r["posting_id"]) for r in calibrate.read_rows(first)}

        second = tmp_path / "b.csv"
        calibrate.write_template(conn, second, 20, exclude_ids=already)
        holdout = {int(r["posting_id"]) for r in calibrate.read_rows(second)}

        assert not (already & holdout), "holdout overlaps the tuning set"

    def test_still_stratifies_after_exclusion(self, conn, tmp_path):
        seed(conn, POOL)
        first = tmp_path / "a.csv"
        calibrate.write_template(conn, first, 20)
        already = {int(r["posting_id"]) for r in calibrate.read_rows(first)}

        second = tmp_path / "b.csv"
        calibrate.write_template(conn, second, 20, exclude_ids=already)
        lanes = {r["lane"] for r in calibrate.read_rows(second)}
        assert "strong-lane" in lanes and "generic-swe" in lanes

    def test_raises_when_nothing_is_left(self, conn, tmp_path):
        seed(conn, ["Machine Learning Engineer"] * 3)
        everything = {r["id"] for r in conn.execute(
            "SELECT id FROM postings")}
        with pytest.raises(RuntimeError, match="nothing left"):
            calibrate.write_template(conn, tmp_path / "c.csv", 20,
                                     exclude_ids=everything)


class TestSpreadStatistic:
    def test_stdev_flags_a_clustered_set(self):
        assert calibrate._stdev([6, 6, 7, 7, 6, 7]) < 1.5

    def test_stdev_accepts_a_spread_set(self):
        assert calibrate._stdev([1, 2, 4, 6, 7, 9]) > 1.5

    def test_stdev_handles_degenerate_input(self):
        assert calibrate._stdev([]) == 0.0
        assert calibrate._stdev([5]) == 0.0


class TestEncodingTolerance:
    """The sheet is hand-edited, and Windows editors write cp1252."""

    def test_reads_a_cp1252_sheet(self, tmp_path):
        path = tmp_path / "calibration.csv"
        # 0x92 is a cp1252 curly apostrophe — exactly what an editor wrote
        # into a real hand-scored note and what broke a UTF-8-only read.
        path.write_bytes(
            b"posting_id,company,title,lane,url,my_score,notes\n"
            b"1,Co,Eng,other,https://x,3,\"haven\x92t done this yet\"\n")
        hand = calibrate.load_hand_scores(path)
        assert len(hand) == 1
        # A legacy single-column sheet was scored on a "should I apply"
        # basis, so it maps to my_apply and leaves my_fit unknown.
        assert hand[0]["my_apply"] == 3
        assert hand[0]["my_fit"] is None

    def test_reads_a_utf8_bom_sheet(self, tmp_path):
        path = tmp_path / "calibration.csv"
        path.write_text("posting_id,company,title,lane,url,my_score,notes\n"
                        "1,Co,Eng,other,https://x,5,fine\n",
                        encoding="utf-8-sig")
        rows = calibrate.read_rows(path)
        assert rows[0]["posting_id"] == "1", "BOM must not corrupt the header"


class TestNeverDestroysHandScoring:
    """Overwriting a scored sheet lost both the scores and the record of
    which postings were used — which let a later holdout re-draw them."""

    def test_refuses_to_overwrite_a_scored_sheet(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)

        rows = calibrate.read_rows(path)
        rows[0]["my_apply"] = "7"
        with path.open("w", newline="", encoding="utf-8-sig") as h:
            w = csv.DictWriter(h, fieldnames=calibrate.COLUMNS)
            w.writeheader()
            w.writerows(rows)

        with pytest.raises(RuntimeError, match="Refusing to overwrite"):
            calibrate.write_template(conn, path, 20)

    def test_overwrites_a_blank_sheet_freely(self, conn, tmp_path):
        seed(conn, POOL)
        path = tmp_path / "calibration.csv"
        calibrate.write_template(conn, path, 20)
        assert calibrate.write_template(conn, path, 20) == 20


class TestNonNumericCells:
    """Hand-edited sheets contain human values, not just integers."""

    def test_na_is_treated_as_unscored_not_a_crash(self, tmp_path):
        path = tmp_path / "c.csv"
        path.write_text(
            "posting_id,company,title,lane,url,my_fit,my_apply,notes\n"
            "1,Co,Live role,other,https://x,6,8,fine\n"
            "2,Co,Dead link,other,https://y,n/a,n/a,Page not found\n",
            encoding="utf-8")
        hand = calibrate.load_hand_scores(path)
        assert len(hand) == 1, "the n/a row should be skipped, not fatal"
        assert hand[0]["my_fit"] == 6 and hand[0]["my_apply"] == 8

    def test_tolerates_other_human_placeholders(self, tmp_path):
        path = tmp_path / "c.csv"
        path.write_text(
            "posting_id,company,title,lane,url,my_fit,my_apply,notes\n"
            "1,Co,A,other,https://x,?,-,couldn't tell\n"
            "2,Co,B,other,https://y,5,5,fine\n", encoding="utf-8")
        assert len(calibrate.load_hand_scores(path)) == 1

    def test_one_axis_scored_is_enough(self, tmp_path):
        path = tmp_path / "c.csv"
        path.write_text(
            "posting_id,company,title,lane,url,my_fit,my_apply,notes\n"
            "1,Co,A,other,https://x,4,n/a,only fit judged\n",
            encoding="utf-8")
        hand = calibrate.load_hand_scores(path)
        assert hand[0]["my_fit"] == 4 and hand[0]["my_apply"] is None


class TestHandScoreLoading:
    def test_rejects_an_unscored_sheet(self, tmp_path):
        path = tmp_path / "calibration.csv"
        path.write_text("posting_id,company,title,lane,url,my_score,notes\n"
                        "1,Co,Eng,other,https://x,,\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="no filled-in scores"):
            calibrate.load_hand_scores(path)

    def test_reads_scored_rows_only(self, tmp_path):
        path = tmp_path / "calibration.csv"
        path.write_text("posting_id,company,title,lane,url,my_score,notes\n"
                        "1,Co,Eng,other,https://x,7,good\n"
                        "2,Co,Eng2,other,https://y,,\n", encoding="utf-8")
        hand = calibrate.load_hand_scores(path)
        assert len(hand) == 1 and hand[0]["my_apply"] == 7
