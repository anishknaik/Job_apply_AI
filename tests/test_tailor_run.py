"""The tailoring run: queue ordering, versioning, and the sheet write-back.

No network. The sheet is faked; the queue is built in an in-memory database.
"""
import pytest

from jobpipe import db, pipeline_tailor as pt, sheets
from test_sheets import FakeWorksheet, header_row


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def seed(conn, company, score, source_id, status="queued"):
    from jobpipe.normalize import Posting
    p = Posting(ats_type="ashby", source_id=source_id, company=company,
                title=f"ML Engineer {source_id}", location="NYC",
                remote_flag=False, description="", url=f"u{source_id}",
                posted_at=None)
    pid, _ = db.upsert_posting(conn, p, p.content_hash)
    db.set_stage_a(conn, pid, True, "passed")
    db.insert_score(conn, pid, {
        "overall": score, "apply_score": score, "skills_match": score,
        "seniority_fit": score, "domain_relevance": score, "dealbreakers": [],
        "reasoning": "r", "apply_reason": ""}, model="m")
    if status:
        db.record_status(conn, pid, status)
    conn.commit()
    return pid


class TestQueueOrdering:
    """§1.2: tier ascending, then apply_score descending. The queue will
    exceed what gets applied to, so a partial run must be the right one."""

    def test_tier_outranks_score(self, conn):
        """A tier 3 company's 8 must not outrank a tier 1 company's 7 —
        apply_score cannot express this because it scores the role, not the
        employer."""
        seed(conn, "TierThree", 8, "1")
        seed(conn, "TierOne", 7, "2")
        queue = db.postings_with_status(
            conn, "queued", tiers={"TierOne": 1, "TierThree": 3})
        assert [r["company"] for r in queue] == ["TierOne", "TierThree"]

    def test_score_breaks_ties_within_a_tier(self, conn):
        seed(conn, "Low", 6, "1")
        seed(conn, "High", 9, "2")
        queue = db.postings_with_status(conn, "queued",
                                        tiers={"Low": 1, "High": 1})
        assert [r["company"] for r in queue] == ["High", "Low"]

    def test_an_untiered_company_sorts_last_not_crashes(self, conn):
        seed(conn, "Known", 5, "1")
        seed(conn, "Unlisted", 9, "2")
        queue = db.postings_with_status(conn, "queued", tiers={"Known": 2})
        assert [r["company"] for r in queue] == ["Known", "Unlisted"]

    def test_only_the_named_status_is_returned(self, conn):
        seed(conn, "Queued", 9, "1", status="queued")
        seed(conn, "Applied", 9, "2", status="applied")
        queue = db.postings_with_status(conn, "queued")
        assert [r["company"] for r in queue] == ["Queued"]

    def test_it_reads_the_latest_status_only(self, conn):
        """A role that moved queued -> drafted must leave the queue."""
        pid = seed(conn, "Moved", 9, "1", status="queued")
        db.record_status(conn, pid, "drafted", previous="queued")
        conn.commit()
        assert db.postings_with_status(conn, "queued") == []

    def test_limit_takes_the_top_of_the_order(self, conn):
        seed(conn, "A", 9, "1")
        seed(conn, "B", 8, "2")
        seed(conn, "C", 7, "3")
        queue = db.postings_with_status(conn, "queued",
                                        tiers={"A": 1, "B": 1, "C": 1},
                                        limit=2)
        assert [r["company"] for r in queue] == ["A", "B"]


class TestResumeVersion:
    def test_it_encodes_variant_and_date(self):
        v = pt.resume_version("ai_ml", ["a", "b"], "2026-08-14T10:00:00+00:00")
        assert v.startswith("ai_ml-2026-08-14-")

    def test_different_bullets_give_different_versions(self):
        when = "2026-08-14T10:00:00+00:00"
        assert pt.resume_version("ai_ml", ["a"], when) != \
            pt.resume_version("ai_ml", ["b"], when)

    def test_the_same_selection_is_reproducible(self):
        when = "2026-08-14T10:00:00+00:00"
        assert pt.resume_version("ai_ml", ["a", "b"], when) == \
            pt.resume_version("ai_ml", ["a", "b"], when)

    def test_order_is_significant(self):
        """A reordered resume is a different document."""
        when = "2026-08-14T10:00:00+00:00"
        assert pt.resume_version("ai_ml", ["a", "b"], when) != \
            pt.resume_version("ai_ml", ["b", "a"], when)


class TestSheetWriteBack:
    """The one place the pipeline writes a human-owned column."""

    def _sheet(self, status="Queued"):
        header = header_row()
        row = [""] * len(header)
        row[header.index("url")] = "https://x/1"
        row[header.index("status")] = status
        row[header.index("company")] = "Cohere"
        return FakeWorksheet([header, row])

    def test_it_writes_when_the_expectation_holds(self):
        ws = self._sheet("Queued")
        assert sheets.set_cells(ws, "https://x/1",
                                {"status": "Drafted",
                                 "resume_version": "ai_ml-2026-08-14-abc123"},
                                expect={"status": "Queued"})
        header = ws.values[0]
        assert ws.values[1][header.index("status")] == "Drafted"
        assert ws.values[1][header.index("resume_version")].startswith("ai_ml")

    def test_it_refuses_when_the_human_has_edited_since(self):
        """A batch of fifteen takes minutes; the sheet can change underneath
        it. Writing unconditionally would silently revert the edit."""
        ws = self._sheet("Applied")
        assert not sheets.set_cells(ws, "https://x/1", {"status": "Drafted"},
                                    expect={"status": "Queued"})
        assert ws.values[1][ws.values[0].index("status")] == "Applied"

    def test_the_comparison_ignores_case_and_padding(self):
        ws = self._sheet("  queued ")
        assert sheets.set_cells(ws, "https://x/1", {"status": "Drafted"},
                                expect={"status": "Queued"})

    def test_an_unknown_url_writes_nothing(self):
        ws = self._sheet()
        assert not sheets.set_cells(ws, "https://nope/9",
                                    {"status": "Drafted"})

    def test_it_touches_only_the_named_columns(self):
        ws = self._sheet()
        header = ws.values[0]
        ws.values[1][header.index("notes")] = "my note"
        sheets.set_cells(ws, "https://x/1", {"status": "Drafted"},
                         expect={"status": "Queued"})
        assert ws.values[1][header.index("notes")] == "my note"

    def test_sync_still_never_writes_status(self):
        """Phase 1's rule is unchanged: only `set_cells` may, and `sync` does
        not call it."""
        assert "status" in sheets.HUMAN_OWNED
        assert "resume_version" in sheets.HUMAN_OWNED


class TestApplicationFolder:
    def test_the_folder_is_derived_from_company_and_title(self):
        row = {"company": "Cohere", "title": "Software Engineer, Agents"}
        assert pt.application_dir(row).name == \
            "cohere-software-engineer-agents"

    def test_it_is_filesystem_safe(self):
        row = {"company": "AT&T / Labs", "title": "ML Eng (Sr.)"}
        name = pt.application_dir(row).name
        assert all(c.isalnum() or c == "-" for c in name)
