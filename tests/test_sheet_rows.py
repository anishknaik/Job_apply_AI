"""Sheet surfacing: rank, not threshold.

Calibration showed a fixed cutoff cannot deliver the spec's "5-20 new roles"
reliably — the model's positives pile onto exactly 6, so one point of drift
swings the sheet between three rows and twelve.
"""
from collections import Counter

import pytest

from jobpipe import db as _db


class TestPerCompanyCap:
    """Addendum 2 §1: top 25, max 3 per company, capped DURING selection.

    Without the cap one employer with forty open reqs crowds out every
    mid-size company. Applying it after truncation is the subtle bug the
    addendum calls out: slots freed by a dropped row never get refilled, so
    the sheet comes back short.
    """

    def _seed(self, conn, spec):
        """spec: [(company, n_postings, apply_score), ...]"""
        from jobpipe.normalize import Posting
        n = 0
        for company, count, score in spec:
            for _ in range(count):
                n += 1
                p = Posting(ats_type="ashby", source_id=str(n),
                            company=company, title=f"ML Engineer {n}",
                            location="NYC", remote_flag=False,
                            description="", url=f"u{n}", posted_at=None)
                pid, _hash = _db.upsert_posting(conn, p, p.content_hash)
                _db.set_stage_a(conn, pid, True, "passed")
                _db.insert_score(conn, pid, {
                    "overall": score, "apply_score": score,
                    "skills_match": score, "seniority_fit": score,
                    "domain_relevance": score, "dealbreakers": [],
                    "reasoning": "r", "apply_reason": ""}, model="m")
        conn.commit()

    def test_no_company_exceeds_the_cap(self, conn):
        self._seed(conn, [("Bigco", 40, 9), ("Smallco", 2, 8)])
        rows = _db.get_sheet_rows(conn, limit=25, per_company=3)
        counts = Counter(r["company"] for r in rows)
        assert counts["Bigco"] == 3

    def test_the_sheet_still_fills_to_the_limit(self, conn):
        """The bug the addendum warns about: cap after truncation leaves the
        sheet short, because dropped rows do not free their slot."""
        self._seed(conn, [(f"Co{i}", 5, 9) for i in range(10)])
        rows = _db.get_sheet_rows(conn, limit=25, per_company=3)
        assert len(rows) == 25

    def test_a_dominant_company_cannot_crowd_out_the_rest(self, conn):
        """40 reqs at 9 from one company, 2 at 8 from another. Without the cap
        the small company never appears."""
        self._seed(conn, [("Bigco", 40, 9), ("Smallco", 2, 8)])
        companies = {r["company"] for r in
                     _db.get_sheet_rows(conn, limit=25, per_company=3)}
        assert "Smallco" in companies

    def test_twenty_five_slots_guarantee_nine_distinct_companies(self, conn):
        """The addendum's stated reason for 25-with-a-3-cap."""
        self._seed(conn, [(f"Co{i}", 8, 9) for i in range(12)])
        rows = _db.get_sheet_rows(conn, limit=25, per_company=3)
        assert len({r["company"] for r in rows}) >= 9

    def test_ranking_is_still_respected_within_the_cap(self, conn):
        """The cap must take a company's BEST postings, not its first."""
        self._seed(conn, [("Bigco", 3, 5)])
        self._seed(conn, [("Bigco", 2, 9)])
        rows = [r for r in _db.get_sheet_rows(conn, limit=25, per_company=3)
                if r["company"] == "Bigco"]
        assert sorted((r["apply_score"] for r in rows), reverse=True)[:2] \
            == [9, 9]

    def test_the_cap_can_be_disabled(self, conn):
        self._seed(conn, [("Bigco", 40, 9)])
        rows = _db.get_sheet_rows(conn, limit=25, per_company=None)
        assert len(rows) == 25
        assert all(r["company"] == "Bigco" for r in rows)


class TestScoreRoundTrip:
    """Every field the model returns must survive the write.

    `engagement` was added to the schema, the migration and the sheet query
    but missed in both INSERT statements. 220 postings were scored with it and
    every value came back NULL — no error, no warning, nothing in a log. This
    is written against the schema rather than a hand-listed set of columns so
    the next added field cannot repeat it.
    """

    def _payload(self):
        from jobpipe import scoring
        sample = {"archetype": "ai_ml_engineer", "engagement": "consulting",
                  "legitimacy": "ok", "legitimacy_reason": "",
                  "reasoning": "r", "skills_match": 7, "seniority_fit": 6,
                  "domain_relevance": 8, "dealbreakers": ["x"], "overall": 6,
                  "apply_score": 7, "apply_reason": "why"}
        missing = set(scoring.SCORE_SCHEMA["required"]) - set(sample)
        assert not missing, f"test payload is missing {missing}"
        return sample

    def test_every_scored_field_is_persisted(self, conn):
        from jobpipe.normalize import Posting
        from jobpipe import scoring

        p = Posting(ats_type="ashby", source_id="1", company="X",
                    title="ML Engineer", location="NYC", remote_flag=False,
                    description="", url="u1", posted_at=None)
        pid, _ = _db.upsert_posting(conn, p, p.content_hash)
        _db.insert_score(conn, pid, self._payload(), model="m")
        conn.commit()

        stored = conn.execute("SELECT * FROM scores WHERE posting_id = ?",
                              (pid,)).fetchone()
        columns = {row["name"] for row in
                   conn.execute("PRAGMA table_info(scores)")}
        for field in scoring.SCORE_SCHEMA["required"]:
            if field not in columns:
                continue          # not every scored field has a column
            assert stored[field] is not None, \
                f"{field} was returned by the model but stored as NULL"

    def test_a_repost_carries_every_field_forward(self, conn):
        from jobpipe.normalize import Posting

        rows = []
        for source_id in ("1", "2"):
            p = Posting(ats_type="ashby", source_id=source_id, company="X",
                        title="ML Engineer", location="NYC",
                        remote_flag=False, description="", url=f"u{source_id}",
                        posted_at=None)
            pid, _ = _db.upsert_posting(conn, p, p.content_hash)
            rows.append(pid)
        _db.insert_score(conn, rows[0], self._payload(), model="m")
        assert _db.copy_score(conn, rows[0], rows[1])
        conn.commit()

        a, b = (conn.execute("SELECT * FROM scores WHERE posting_id = ?",
                             (pid,)).fetchone() for pid in rows)
        assert a["engagement"] == b["engagement"] == "consulting"
        assert a["archetype"] == b["archetype"]


class TestStaleness:
    """Addendum 4.1 without a browser: a posting that has dropped out of its
    feed stops surfacing, using last_seen_at rather than a Playwright check.
    Ashby returns 200 for dead postings, so the browser check would be wrong.
    """

    def test_cutoff_matches_the_stored_timestamp_format(self):
        """The bug this guards: SQLite's datetime('now') renders with a space
        separator and no offset, so comparing it against an isoformat string
        compares ' ' with 'T' and silently returns the wrong rows."""
        now, cutoff = _db.utcnow(), _db.utc_days_ago(7)
        assert cutoff < now
        assert "T" in cutoff and cutoff.endswith("+00:00")
        assert len(cutoff) == len(now)

    def test_a_posting_not_seen_recently_is_dropped(self, conn):
        from jobpipe.normalize import Posting
        for source_id, last_seen in (("fresh", _db.utcnow()),
                                     ("gone", _db.utc_days_ago(30))):
            p = Posting(ats_type="ashby", source_id=source_id, company="X",
                        title=f"ML Engineer {source_id}", location="NYC",
                        remote_flag=False, description="", url=source_id,
                        posted_at=None)
            pid, _ = _db.upsert_posting(conn, p, p.content_hash)
            _db.set_stage_a(conn, pid, True, "passed")
            _db.insert_score(conn, pid, {
                "overall": 9, "apply_score": 9, "skills_match": 9,
                "seniority_fit": 9, "domain_relevance": 9, "dealbreakers": [],
                "reasoning": "r", "apply_reason": ""}, model="m")
            conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?",
                         (last_seen, pid))
        conn.commit()

        urls = {r["url"] for r in _db.get_sheet_rows(conn, max_stale_days=7)}
        assert urls == {"fresh"}

    def test_staleness_can_be_switched_off(self, conn):
        from jobpipe.normalize import Posting
        p = Posting(ats_type="ashby", source_id="gone", company="X",
                    title="ML Engineer", location="NYC", remote_flag=False,
                    description="", url="gone", posted_at=None)
        pid, _ = _db.upsert_posting(conn, p, p.content_hash)
        _db.set_stage_a(conn, pid, True, "passed")
        _db.insert_score(conn, pid, {
            "overall": 9, "apply_score": 9, "skills_match": 9,
            "seniority_fit": 9, "domain_relevance": 9, "dealbreakers": [],
            "reasoning": "r", "apply_reason": ""}, model="m")
        conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?",
                     (_db.utc_days_ago(90), pid))
        conn.commit()
        assert _db.get_sheet_rows(conn, max_stale_days=None)
        assert not _db.get_sheet_rows(conn, max_stale_days=7)

from jobpipe import db
from jobpipe.normalize import Posting


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def add(conn, company, title, overall, apply_score, seen="2026-08-13T00:00:00+00:00"):
    posting = Posting(ats_type="ashby", source_id=f"{company}-{title}",
                      company=company, title=title, location="New York",
                      remote_flag=False, description="", url=f"https://x/{title}",
                      posted_at=None)
    pid, _ = db.upsert_posting(conn, posting, posting.content_hash, seen_at=seen)
    db.set_stage_a(conn, pid, True, "passed")
    db.insert_score(conn, pid, {
        "skills_match": 5, "seniority_fit": 5, "domain_relevance": 5,
        "overall": overall, "apply_score": apply_score, "apply_reason": "",
        "dealbreakers": [], "reasoning": "r",
    }, model="gemini-3.1-flash-lite")
    return pid


class TestRanking:
    def test_returns_the_top_n_by_apply_score(self, conn):
        # All above the floor, so only the limit is doing the work here.
        for i in range(30):
            add(conn, f"Co{i}", f"Role {i}", overall=5,
                apply_score=4 + (i % 7))
        rows = db.get_sheet_rows(conn, limit=20)
        assert len(rows) == 20
        scores = [r["apply_score"] for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_the_floor_removes_rows_before_the_limit_applies(self, conn):
        for i in range(30):
            add(conn, f"Co{i}", f"Role {i}", overall=5, apply_score=i % 10)
        rows = db.get_sheet_rows(conn, limit=20, min_score=4)
        assert len(rows) == 18, "12 postings score 0-3 and must not surface"
        assert min(r["apply_score"] for r in rows) >= 4

    def test_ranks_on_apply_not_overall(self, conn):
        add(conn, "A", "high fit low apply", overall=9, apply_score=2)
        add(conn, "B", "low fit high apply", overall=3, apply_score=9)
        rows = db.get_sheet_rows(conn, limit=5)
        assert rows[0]["company"] == "B", "the sheet answers 'should I apply'"

    def test_overall_still_visible_alongside(self, conn):
        add(conn, "A", "role", overall=3, apply_score=8)
        row = db.get_sheet_rows(conn, limit=5)[0]
        assert row["overall"] == 3 and row["apply_score"] == 8

    def test_a_thin_day_is_not_padded_with_rejects(self, conn):
        """The floor matters more than filling the quota."""
        add(conn, "A", "good", overall=6, apply_score=8)
        add(conn, "B", "bad", overall=1, apply_score=1)
        add(conn, "C", "awful", overall=1, apply_score=2)
        rows = db.get_sheet_rows(conn, limit=20, min_score=4)
        assert len(rows) == 1

    def test_limit_is_respected_even_when_everything_qualifies(self, conn):
        for i in range(40):
            add(conn, f"Co{i}", f"Role {i}", overall=8, apply_score=8)
        assert len(db.get_sheet_rows(conn, limit=20)) == 20

    def test_is_stable_against_a_one_point_drift(self, conn):
        """The failure that motivated ranking: with a cutoff at 6, scores
        piled on 6 make the sheet size swing wildly."""
        for i in range(25):
            add(conn, f"Co{i}", f"Role {i}", overall=5, apply_score=6)
        before = db.get_sheet_rows(conn, limit=20)

        conn.execute("UPDATE scores SET apply_score = 5")
        after = db.get_sheet_rows(conn, limit=20, min_score=4)
        assert len(before) == len(after) == 20, \
            "sheet size must not depend on where the scores happen to land"


class TestTierThresholds:
    def test_tier_three_needs_a_higher_score(self, conn):
        add(conn, "Dream", "role", overall=6, apply_score=6)
        add(conn, "Maybe", "role", overall=6, apply_score=6)
        rows = db.get_sheet_rows(
            conn, limit=20, tier_thresholds={"Dream": 6, "Maybe": 8})
        assert [r["company"] for r in rows] == ["Dream"]

    def test_tier_three_still_surfaces_when_strong(self, conn):
        add(conn, "Maybe", "role", overall=8, apply_score=9)
        rows = db.get_sheet_rows(conn, limit=20,
                                 tier_thresholds={"Maybe": 8})
        assert len(rows) == 1


class TestBackwardCompatibility:
    def test_rows_scored_before_apply_score_existed_still_surface(self, conn):
        pid = add(conn, "A", "role", overall=7, apply_score=7)
        conn.execute("UPDATE scores SET apply_score = NULL WHERE posting_id = ?",
                     (pid,))
        rows = db.get_sheet_rows(conn, limit=20)
        assert len(rows) == 1
        assert rows[0]["apply_score"] == 7, "should fall back to overall"
