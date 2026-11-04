"""Employer edits to a live posting: update the text, rescore only when the
years or skills asked for moved (Anish's call, 2026-11-04)."""
import dataclasses

import pytest

from jobpipe import db, filters, india, pipeline, posting_changes
from jobpipe.normalize import Posting

T0 = "2026-08-01T00:00:00+00:00"
T1 = "2026-09-01T00:00:00+00:00"
T2 = "2026-10-01T00:00:00+00:00"

BODY = ("We build developer tools. Requirements: 3+ years of experience "
        "with Python and PostgreSQL. Nice to have: Kubernetes.")


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def make(description=BODY, title="Software Engineer", location="New York, NY",
         **kw):
    return Posting(
        ats_type="greenhouse", source_id="1", company="Stripe", title=title,
        location=location, remote_flag=False, description=description,
        url="https://example.com/1", posted_at=db.utcnow(), **kw)


def upsert(conn, posting, seen_at):
    changes: list = []
    pid, is_new = db.upsert_posting(conn, posting, posting.content_hash,
                                    seen_at=seen_at, changes=changes)
    return pid, is_new, changes


def score(conn, pid, at):
    db.insert_score(conn, pid, {"overall": 7, "apply_score": 7}, "m")
    conn.execute("UPDATE scores SET scored_at = ? WHERE posting_id = ?",
                 (at, pid))


def row(conn, pid):
    return conn.execute("SELECT * FROM postings WHERE id = ?",
                        (pid,)).fetchone()


class TestSkillsNamed:
    def test_finds_terms_on_word_boundaries(self):
        found = posting_changes.skills_named(
            "Python, C++ and Node.js; PostgreSQL in production")
        assert {"python", "c++", "node.js", "postgresql"} <= found

    def test_english_words_are_not_skills(self):
        # "go", "rest", "spring", "rails", "agents" were all dropped from the
        # vocabulary: a rewritten paragraph would trip them.
        assert posting_changes.skills_named(
            "Go rest assured: spring hiring, guard rails, our agents") == set()

    def test_no_match_inside_a_word(self):
        assert "sql" not in posting_changes.skills_named("mysqlish")


class TestDiff:
    def test_whitespace_is_not_an_edit(self, conn):
        pid, _, _ = upsert(conn, make(), T0)
        _, _, changes = upsert(conn, make(BODY.replace(" ", "  \n")), T1)
        assert changes == []

    def test_boilerplate_edit_updates_but_does_not_rescore(self, conn):
        pid, _, _ = upsert(conn, make(), T0)
        edited = "We are now part of a larger company. " + BODY
        _, _, changes = upsert(conn, make(edited), T1)
        (_, change), = changes
        assert change.fields == ("description",)
        assert not change.needs_rescore
        assert row(conn, pid)["description"] == edited
        assert row(conn, pid)["rescore_requested_at"] is None

    def test_years_change_rescores(self, conn):
        # The LangChain case: "3+ years" became "1-3 years".
        pid, _, _ = upsert(conn, make(), T0)
        _, _, changes = upsert(
            conn, make(BODY.replace("3+ years", "1-3 years")), T1)
        (_, change), = changes
        assert (change.years_before, change.years_after) == (3, 1)
        assert change.needs_rescore
        assert row(conn, pid)["rescore_requested_at"] == T1

    def test_skill_change_rescores(self, conn):
        pid, _, _ = upsert(conn, make(), T0)
        _, _, changes = upsert(
            conn, make(BODY.replace("Kubernetes", "Terraform")), T1)
        (_, change), = changes
        assert change.skills_added == {"terraform"}
        assert change.skills_removed == {"kubernetes"}
        assert change.needs_rescore

    def test_title_change_moves_the_hash(self, conn):
        first = make()
        pid, _, _ = upsert(conn, first, T0)
        renamed = make(title="AI Engineer")
        upsert(conn, renamed, T1)
        stored = row(conn, pid)
        assert stored["title"] == "AI Engineer"
        assert stored["content_hash"] == renamed.content_hash
        assert stored["content_hash"] != first.content_hash

    def test_unchanged_still_only_touches_last_seen(self, conn):
        pid, _, _ = upsert(conn, make(), T0)
        upsert(conn, make(), T1)
        stored = row(conn, pid)
        assert stored["last_seen_at"] == T1
        assert stored["text_changed_at"] is None


class TestStageA:
    def test_an_edit_sends_the_posting_back_through_stage_a(self, conn):
        pid, _, _ = upsert(conn, make(), T0)
        db.set_stage_a(conn, pid, True, "passed")
        upsert(conn, make("Edited. " + BODY), T1)
        assert row(conn, pid)["stage_a_passed"] is None

    def test_a_duplicate_marking_survives_an_edit(self, conn):
        pid, _, _ = upsert(conn, make(), T0)
        db.set_stage_a(conn, pid, False, "duplicate: of 7")
        upsert(conn, make("Edited. " + BODY), T1)
        stored = row(conn, pid)
        assert stored["stage_a_passed"] == 0
        assert stored["stage_a_reason"] == "duplicate: of 7"

    def test_refilter_judges_age_as_of_first_sight(self, conn):
        # A role open for months must not fail max_age_days merely because
        # an edit put it back through Stage A.
        posting = dataclasses.replace(make(), posted_at=T0)
        pid, _, _ = upsert(conn, posting, T0)
        conn.execute("UPDATE postings SET stage_a_passed = NULL WHERE id = ?",
                     (pid,))
        rules = dataclasses.replace(
            filters.load_rules(), max_age_days=30, title_require_any=[],
            title_exclude=[], location_allow=[], location_exclude=[],
            max_years_experience=None, description_exclude=[],
            sponsorship_exclude=[])
        pipeline.filter_all(conn, rules)
        assert row(conn, pid)["stage_a_passed"] == 1


class TestScoringQueue:
    def _passed_and_scored(self, conn, at=T0):
        pid, _, _ = upsert(conn, make(), at)
        db.set_stage_a(conn, pid, True, "passed")
        score(conn, pid, at)
        # get_unscored skips postings off their board for 7 days.
        conn.execute("UPDATE postings SET last_seen_at = ? WHERE id = ?",
                     (db.utcnow(), pid))
        return pid

    def test_scored_posting_is_not_queued(self, conn):
        self._passed_and_scored(conn)
        assert db.get_unscored(conn) == []

    def test_rescore_request_after_the_score_queues_it(self, conn):
        pid = self._passed_and_scored(conn)
        conn.execute("UPDATE postings SET rescore_requested_at = ? "
                     "WHERE id = ?", (T1, pid))
        assert [r["id"] for r in db.get_unscored(conn)] == [pid]

    def test_rescoring_clears_it(self, conn):
        pid = self._passed_and_scored(conn)
        conn.execute("UPDATE postings SET rescore_requested_at = ? "
                     "WHERE id = ?", (T1, pid))
        score(conn, pid, T2)
        assert db.get_unscored(conn) == []

    def test_old_score_survives_until_replaced(self, conn):
        # The sheet must not go blank between the edit and the rescore.
        pid = self._passed_and_scored(conn)
        conn.execute("UPDATE postings SET rescore_requested_at = ? "
                     "WHERE id = ?", (T1, pid))
        assert conn.execute("SELECT overall FROM scores WHERE posting_id = ?",
                            (pid,)).fetchone()[0] == 7

    def test_india_queue_takes_rescores_too(self, conn):
        posting = make(location="Bengaluru, India")
        pid, _, _ = upsert(conn, posting, db.utcnow())
        db.set_stage_a(conn, pid, False, "location")
        score(conn, pid, T0)
        rules = filters.load_rules()
        assert pid not in [r["id"] for r in india.unscored(conn, rules)]
        conn.execute("UPDATE postings SET rescore_requested_at = ? "
                     "WHERE id = ?", (T1, pid))
        assert pid in [r["id"] for r in india.unscored(conn, rules)]
