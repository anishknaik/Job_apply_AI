"""Dedupe tests — the single most important behaviour in Phase 1.

Running the pipeline twice must produce zero new postings and zero scoring
calls. Anything less means paying to re-score the same jobs forever.
"""
import pytest

from jobpipe import db, dedupe
from jobpipe.normalize import Posting


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def make(source_id="1", company="Stripe", title="Software Engineer",
         location="New York, NY", ats="greenhouse"):
    return Posting(
        ats_type=ats, source_id=source_id, company=company, title=title,
        location=location, remote_flag=False, description="body",
        url=f"https://example.com/{source_id}",
        # Never a literal date: Stage A drops a posting older than
        # max_age_days, so a fixed one turns into a failure a month later.
        posted_at=db.utcnow(),
    )


class TestLayerOneSourceId:
    def test_first_insert_is_new(self, conn):
        posting = make()
        _, is_new = db.upsert_posting(conn, posting, posting.content_hash)
        assert is_new

    def test_same_source_id_is_not_new(self, conn):
        posting = make()
        first_id, _ = db.upsert_posting(conn, posting, posting.content_hash)
        second_id, is_new = db.upsert_posting(conn, posting, posting.content_hash)
        assert not is_new
        assert first_id == second_id

    def test_reinsert_only_touches_last_seen_at(self, conn):
        posting = make()
        posting_id, _ = db.upsert_posting(conn, posting, posting.content_hash,
                                          seen_at="2026-08-01T00:00:00+00:00")
        db.upsert_posting(conn, posting, posting.content_hash,
                          seen_at="2026-08-09T00:00:00+00:00")
        row = db.get_posting(conn, posting_id)
        assert row["first_seen_at"] == "2026-08-01T00:00:00+00:00"
        assert row["last_seen_at"] == "2026-08-09T00:00:00+00:00"

    def test_same_source_id_on_a_different_ats_is_distinct(self, conn):
        db.upsert_posting(conn, make(ats="greenhouse"), "hash-a")
        _, is_new = db.upsert_posting(conn, make(ats="lever"), "hash-b")
        assert is_new

    def test_a_full_second_run_produces_no_new_postings(self, conn):
        """The spec's headline test, in miniature."""
        board = [make(source_id=str(i)) for i in range(25)]
        first = sum(db.upsert_posting(conn, p, p.content_hash)[1] for p in board)
        second = sum(db.upsert_posting(conn, p, p.content_hash)[1] for p in board)
        assert first == 25
        assert second == 0


class TestLayerTwoContentHash:
    def test_relisted_req_is_detected_as_a_repost(self, conn):
        original = make(source_id="req-1")
        original_id, _ = db.upsert_posting(conn, original, original.content_hash)
        db.set_stage_a(conn, original_id, True, "passed")
        db.insert_score(conn, original_id, {
            "skills_match": 8, "seniority_fit": 7, "domain_relevance": 9,
            "overall": 8, "dealbreakers": [], "reasoning": "good fit",
        }, model="claude-haiku-4-5")

        # Same role, relisted three weeks later under a brand new req id.
        relisted = make(source_id="req-2")
        relisted_id, is_new = db.upsert_posting(conn, relisted,
                                                relisted.content_hash)
        assert is_new, "a new source_id is genuinely a new row"
        assert dedupe.resolve_repost(conn, relisted_id, relisted.content_hash)

        row = db.get_posting(conn, relisted_id)
        assert row["stage_a_passed"] == 0
        assert row["stage_a_reason"].startswith("duplicate:")

    def test_repost_carries_the_existing_score_forward(self, conn):
        original = make(source_id="req-1")
        original_id, _ = db.upsert_posting(conn, original, original.content_hash)
        db.insert_score(conn, original_id, {
            "skills_match": 8, "seniority_fit": 7, "domain_relevance": 9,
            "overall": 8, "dealbreakers": [], "reasoning": "good fit",
        }, model="claude-haiku-4-5")

        relisted = make(source_id="req-2")
        relisted_id, _ = db.upsert_posting(conn, relisted, relisted.content_hash)
        dedupe.resolve_repost(conn, relisted_id, relisted.content_hash)

        carried = conn.execute("SELECT * FROM scores WHERE posting_id = ?",
                               (relisted_id,)).fetchone()
        assert carried["overall"] == 8
        assert carried["reasoning"] == "good fit"

    def test_a_repost_never_enters_the_scoring_queue(self, conn):
        original = make(source_id="req-1")
        original_id, _ = db.upsert_posting(conn, original, original.content_hash)
        db.set_stage_a(conn, original_id, True, "passed")

        relisted = make(source_id="req-2")
        relisted_id, _ = db.upsert_posting(conn, relisted, relisted.content_hash)
        dedupe.resolve_repost(conn, relisted_id, relisted.content_hash)

        queued = [row["id"] for row in db.get_unscored(conn)]
        assert relisted_id not in queued

    def test_different_role_is_not_a_repost(self, conn):
        first = make(source_id="req-1", title="Software Engineer")
        first_id, _ = db.upsert_posting(conn, first, first.content_hash)

        second = make(source_id="req-2", title="Data Scientist")
        second_id, _ = db.upsert_posting(conn, second, second.content_hash)
        assert not dedupe.resolve_repost(conn, second_id, second.content_hash)

    def test_same_title_in_a_different_city_IS_a_duplicate(self, conn):
        """The multi-location case — one req returned once per city."""
        first = make(source_id="req-1", location="New York, NY")
        first_id, _ = db.upsert_posting(conn, first, first.content_hash)

        second = make(source_id="req-2", location="Seattle, WA")
        second_id, _ = db.upsert_posting(conn, second, second.content_hash)
        assert dedupe.resolve_repost(conn, second_id, second.content_hash,
                                     second.location)

class TestMultiLocationCollapse:
    """One req returned once per location must become one row, keeping every
    city — otherwise the pipeline keeps the San Francisco listing, filters it
    out on location, and skips its New York twin as a duplicate."""

    CITIES = ["San Francisco", "Chicago", "New York", "Dallas", "Remote"]

    def _ingest(self, conn):
        ids = []
        for i, city in enumerate(self.CITIES):
            posting = make(source_id=f"req-{i}", company="Harvey",
                           title="Technology Enablement Analyst", location=city)
            posting_id, _ = db.upsert_posting(conn, posting,
                                              posting.content_hash)
            if not dedupe.resolve_repost(conn, posting_id,
                                         posting.content_hash,
                                         posting.location):
                ids.append(posting_id)
        return ids

    def test_five_listings_leave_one_scoreable_row(self, conn):
        canonical_ids = self._ingest(conn)
        assert len(canonical_ids) == 1, "four of the five must be duplicates"
        db.set_stage_a(conn, canonical_ids[0], True, "passed")
        assert len(db.get_unscored(conn)) == 1, \
            "the same req must never be scored five times"

    def test_every_city_is_preserved_on_the_canonical_row(self, conn):
        canonical_ids = self._ingest(conn)
        assert len(canonical_ids) == 1
        location = db.get_posting(conn, canonical_ids[0])["location"]
        for city in self.CITIES:
            assert city in location, f"{city} was lost"

    def test_a_new_york_req_first_seen_in_sf_still_survives_stage_a(self, conn):
        """The failure this whole change exists to prevent."""
        from jobpipe import filters
        rules = filters.load_rules()

        canonical_ids = self._ingest(conn)
        row = db.get_posting(conn, canonical_ids[0])
        passed, reason = filters.evaluate(row, rules)
        assert passed, (
            f"req is open in New York but was dropped: {reason} "
            f"(location={row['location']!r})")

    def test_merging_reopens_stage_a_on_the_canonical_row(self, conn):
        first = make(source_id="req-1", location="San Francisco")
        first_id, _ = db.upsert_posting(conn, first, first.content_hash)
        db.set_stage_a(conn, first_id, False, "location_allow: sf not allowed")

        second = make(source_id="req-2", location="New York")
        second_id, _ = db.upsert_posting(conn, second, second.content_hash)
        dedupe.resolve_repost(conn, second_id, second.content_hash,
                              second.location)

        row = db.get_posting(conn, first_id)
        assert row["stage_a_passed"] is None, \
            "a stale rejection must be cleared so the row is re-evaluated"

    def test_merging_an_already_known_city_is_a_no_op(self, conn):
        first = make(source_id="req-1", location="New York")
        first_id, _ = db.upsert_posting(conn, first, first.content_hash)
        db.set_stage_a(conn, first_id, True, "passed")

        second = make(source_id="req-2", location="New York")
        second_id, _ = db.upsert_posting(conn, second, second.content_hash)
        dedupe.resolve_repost(conn, second_id, second.content_hash,
                              second.location)

        row = db.get_posting(conn, first_id)
        assert row["stage_a_passed"] == 1, \
            "no new location means no reason to re-run Stage A"


class TestWindow:
    def test_repost_window_is_respected(self, conn):
        first = make(source_id="req-1")
        first_id, _ = db.upsert_posting(conn, first, first.content_hash,
                                        seen_at="2020-01-01T00:00:00+00:00")
        second = make(source_id="req-2")
        second_id, _ = db.upsert_posting(conn, second, second.content_hash)
        # Well outside the 60-day window: score it fresh rather than carry
        # a years-old judgement forward.
        assert not dedupe.resolve_repost(conn, second_id, second.content_hash,
                                         window_days=60)
